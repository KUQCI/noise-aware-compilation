"""v3 Phase 1: the Step 6 statistics, the Step 6b baselines and their committed results."""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import circuit_for

from nac_bench import stats_v3
from noise_aware_compilation.devices import StoredDevice, load_device
from noise_aware_compilation.placement import enumerate_placements, interaction_edges
from noise_aware_compilation.stats import Interval, newcombe_difference, verdict, wilson

ROOT = Path(__file__).resolve().parent.parent
REANALYSIS = ROOT / "results_v3" / "reanalysis" / "reanalysis.json"
BASELINES = ROOT / "results_v3" / "reanalysis" / "baselines.json"
MIRROR = ROOT / "results_v3" / "mirror_check.json"


# --- statistics ------------------------------------------------------------------------------


def test_difference_verdict_follows_the_interval_sign() -> None:
    assert stats_v3.difference_verdict(Interval(-0.2, -0.3, -0.01)) == "better"
    assert stats_v3.difference_verdict(Interval(0.2, 0.01, 0.3)) == "worse"
    assert stats_v3.difference_verdict(Interval(0.0, -0.1, 0.1)) == "tie"


def test_overlap_rule_is_stricter_than_the_difference_rule() -> None:
    """The v1 example: overlapping intervals although the difference excludes zero."""

    a, b = (1700, 200_000), (1840, 200_000)
    old = verdict(wilson(*a), wilson(*b))
    new = stats_v3.difference_verdict(newcombe_difference(*a, *b))
    assert (old, new) == ("indistinguishable", "better")


def test_holm_matches_a_hand_computed_example() -> None:
    result = stats_v3.holm([0.01, 0.04, 0.03, 0.005], alpha=0.05)
    assert result.adjusted == pytest.approx((0.03, 0.06, 0.06, 0.02))
    assert result.rejected == (True, False, False, True)


def test_two_proportion_p_matches_the_normal_tail() -> None:
    scipy_stats = pytest.importorskip("scipy.stats")
    s1, n1, s2, n2 = 120, 10_000, 160, 10_000
    pooled = (s1 + s2) / (n1 + n2)
    z = (s1 / n1 - s2 / n2) / math.sqrt(pooled * (1 - pooled) * (2 / n1))
    assert stats_v3.two_proportion_p(s1, n1, s2, n2) == pytest.approx(
        2 * scipy_stats.norm.sf(abs(z)), rel=1e-12
    )
    assert stats_v3.two_proportion_p(0, 100, 0, 100) == 1.0


def test_cross_fitting_removes_false_headroom_among_identical_placements() -> None:
    """Step 6.2: 80 identical placements look separated in-sample, not cross-fitted."""

    rng = np.random.default_rng(20261108)
    p, n, k, reps = 0.01, 200_000, 80, 200
    in_sample, cross_fitted, bias = 0, 0, []
    for _ in range(reps):
        f = rng.binomial(n, p, size=(k, 3))
        per_seed = [[(int(f[i, s]), n) for s in range(3)] for i in range(k)]
        pooled = f.sum(axis=1)
        best, worst = int(pooled.argmin()), int(pooled.argmax())
        if verdict(wilson(int(pooled[best]), 3 * n), wilson(int(pooled[worst]), 3 * n)) != (
            "indistinguishable"
        ):
            in_sample += 1
        fit = stats_v3.cross_fit(per_seed)
        cross_fitted += fit.separated
        bias.append(fit.oracle_bias)
    assert in_sample / reps > 0.8
    assert cross_fitted / reps < 0.1
    assert float(np.mean(bias)) < -0.01  # the in-sample minimum is biased low


def test_cross_fit_regret_is_zero_when_the_choice_is_every_folds_best() -> None:
    per_seed = [[(100, 10_000)] * 3, [(150, 10_000)] * 3, [(200, 10_000)] * 3]
    fit = stats_v3.cross_fit(per_seed)
    assert [f.best for f in fit.folds] == [0, 0, 0]
    regret = stats_v3.cross_fit_regret(per_seed, fit, 0)
    assert (regret.absolute.estimate, regret.absolute.low, regret.absolute.high) == (0, 0, 0)
    worse = stats_v3.cross_fit_regret(per_seed, fit, 2)
    assert worse.relative.estimate == pytest.approx(1.0)
    assert worse.verdict == "worse"


def test_retained_gain_reports_the_ratio_only_when_the_gain_is_stable() -> None:
    failures, shots = [400, 200, 395], [100_000] * 3
    stable = stats_v3.retained_gain(failures, shots, 0, 1, [1, 1, 2, 1], 500, 1)
    assert stable.stable and stable.ratio is not None
    assert stable.retained.estimate == pytest.approx((4 * 400 - 3 * 200 - 395) / 4 / 100_000)
    unstable = stats_v3.retained_gain(failures, shots, 0, 2, [2, 2], 500, 1)
    assert not unstable.stable and unstable.ratio is None


# --- Step 6b baselines -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("device", "fake"),
    [("jakarta", "FakeJakartaV2"), ("hanoi", "FakeHanoiV2"), ("algiers", "FakeAlgiers")],
)
def test_native_target_equals_the_fake_backends_target_on_live_couplers(
    device: str, fake: str
) -> None:
    pytest.importorskip("qiskit")
    runtime = pytest.importorskip("qiskit_ibm_runtime.fake_provider")
    from nac_bench.baselines_v3 import native_target

    stored = load_device(device)
    ours = native_target(stored.snapshot, stored.device)
    theirs = getattr(runtime, fake)().target
    checked = 0
    for op in ours.operation_names:
        for qargs, props in ours[op].items():
            other = theirs[op][qargs]
            assert (props.error is None) == (other.error is None), (op, qargs)
            if props.error is not None:
                assert props.error == other.error, (op, qargs)
            checked += 1
    removed = {tuple(c["pair"]) for c in stored.device.metadata["removed_couplers"]}
    two_q = next(n for n in ("cx", "cz") if n in theirs.operation_names)
    missing = {tuple(sorted(q)) for q in theirs[two_q] if q not in ours[two_q]}
    assert missing == {tuple(sorted(p)) for p in removed}
    assert checked > 0


def test_placed_detects_a_routed_circuit() -> None:
    pytest.importorskip("qiskit")
    from qiskit import transpile

    from nac_bench.baselines_v3 import native_target, placed
    from nac_bench.qiskit_layout import qiskit_circuit

    stored = load_device("hanoi")
    circuit = circuit_for(3, "Z")
    qc = qiskit_circuit(circuit)
    target = native_target(stored.snapshot, stored.device)
    edges = sorted(interaction_edges(circuit))
    good = transpile(qc, target=target, initial_layout=[4, 1, 2, 3, 5], optimization_level=0)
    assert placed(qc, good, edges).swap_free
    assert placed(qc, good, edges).mapping == (4, 1, 2, 3, 5)
    bad = transpile(
        qc, target=target, initial_layout=[0, 1, 2, 3, 26], optimization_level=0, seed_transpiler=1
    )
    assert not placed(qc, bad, edges).swap_free


@pytest.mark.parametrize("label", ["A", "B"])
def test_snapshot_backend_reproduces_mapomatic_on_the_fake_backend(label: str) -> None:
    """FakeJakarta has no removed couplers, so mapomatic must give identical costs there."""

    pytest.importorskip("mapomatic")
    runtime = pytest.importorskip("qiskit_ibm_runtime.fake_provider")
    import warnings

    import mapomatic as mm
    from qiskit import transpile

    from nac_bench.baselines_v3 import SnapshotBackend, native_target
    from nac_bench.qiskit_layout import qiskit_circuit

    stored = load_device("jakarta")
    circuit = circuit_for(3, "Z" if label == "A" else "X")
    qc = qiskit_circuit(circuit)
    target = native_target(stored.snapshot, stored.device)
    fake = runtime.FakeJakartaV2()
    ours = SnapshotBackend("jakarta", target)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        small = mm.deflate_circuit(
            transpile(qc, target=target, optimization_level=3, seed_transpiler=1)
        )
        layouts = mm.matching_layouts(small, fake)
        a = sorted((tuple(lay), c) for lay, c in mm.evaluate_layouts(small, layouts, ours))
        b = sorted((tuple(lay), c) for lay, c in mm.evaluate_layouts(small, layouts, fake))
    assert len(a) == len(enumerate_placements(circuit, stored.device))
    assert [x for x, _ in a] == [x for x, _ in b]
    assert [c for _, c in a] == pytest.approx([c for _, c in b], rel=1e-12)


def test_qiskit_default_choices_are_candidates_with_a_named_pass(
    devices: dict[str, StoredDevice],
) -> None:
    pytest.importorskip("qiskit")
    from nac_bench.baselines_v3 import LEVELS, native_target, qiskit_default
    from nac_bench.qiskit_layout import qiskit_circuit

    stored = devices["hanoi"]
    circuit = circuit_for(5, "Z")
    candidates = set(enumerate_placements(circuit, stored.device))
    target = native_target(stored.snapshot, stored.device)
    for level in LEVELS:
        c = qiskit_default(
            qiskit_circuit(circuit),
            target,
            level,
            7,
            sorted(interaction_edges(circuit)),
            candidates,
        )
        assert c.failure is None and c.swap_free
        assert c.layout_pass in ("TrivialLayout", "VF2Layout", "SabreLayout", "VF2PostLayout")


def test_mirror_check_swaps_only_the_cx_layers() -> None:
    from nac_bench.mirror_check import swap_cx_layers

    circuit = circuit_for(5, "Z")
    swapped = swap_cx_layers(circuit)
    a, b = circuit.to_stim(), swapped.to_stim()
    assert a.num_detectors == b.num_detectors and a.num_observables == b.num_observables
    assert sorted(str(i) for i in a.flattened()) == sorted(str(i) for i in b.flattened())
    assert str(a) != str(b)


# --- committed results -----------------------------------------------------------------------


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        pytest.skip(f"{path.relative_to(ROOT)} not committed")
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _is_ancestor(commit: str) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd=ROOT, check=False
        ).returncode
        == 0
    )


@pytest.mark.results
def test_reanalysis_counts_the_changed_verdicts() -> None:
    result = _load(REANALYSIS)
    changed = result["changed_verdicts"]
    assert sum(1 for r in changed if r["version"] == "v1") == 7
    own_v2 = [
        r
        for r in changed
        if r["version"] == "v2"
        and r["method"] in ("v1", "decoder-graph")
        and r["reference"] in ("fixed", "average", "lowest-summed-error", "qiskit-vf2")
    ]
    assert len(own_v2) == 3
    for r in changed:
        assert r["old_verdict"] == "indistinguishable"
        a = r["absolute"]
        assert (a["high"] < 0) or (a["low"] > 0)


@pytest.mark.results
def test_reanalysis_families_and_new_baselines() -> None:
    result = _load(REANALYSIS)
    assert {k: v["size"] for k, v in result["holm"].items()} == {
        "v1-headline": 11,
        "v2-headline": 40,
    }
    for name, picks in result["choices"].items():
        for method in ("qiskit-default-l1", "qiskit-default-l2", "qiskit-default-l3", "mapomatic"):
            assert picks[method] is not None, (name, method)
    baselines = _load(BASELINES)
    for record in baselines["cases"].values():
        assert record["mapomatic"]["failure"] is None
        assert record["mapomatic"]["num_layouts"] == len(record["mapomatic"]["candidate_costs"])


@pytest.mark.results
def test_reanalysis_provenance_is_clean() -> None:
    for path in (REANALYSIS, BASELINES):
        prov = _load(path)["provenance"]
        assert prov["package_git_dirty"] is False
        assert _is_ancestor(prov["package_git_commit"])
        assert prov["machine"]["cpu_model"]
        assert prov["versions"]["mapomatic"] == "0.14.0"


@pytest.mark.results
def test_mirror_check_results_are_complete() -> None:
    result = _load(MIRROR)
    config = json.loads((ROOT / "configs_v3" / "mirror_check.json").read_text(encoding="utf-8"))
    assert result["mapping"] == config["mapping"]
    assert result["mirror"] == list(reversed(config["mapping"]))
    for rate in result["rates"].values():
        assert rate["shots"] == len(config["base_seeds"]) * config["shots_per_seed"]
    assert result["provenance"]["package_git_dirty"] is False
    assert _is_ancestor(result["provenance"]["package_git_commit"])
