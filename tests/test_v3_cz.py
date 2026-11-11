"""v3 CZ support (SPEC_V3.md R3-C1 to R3-C3, R3-C9): T3-C1 to T3-C3 on the development devices."""

from __future__ import annotations

import importlib.util
import json
import statistics
import subprocess
from pathlib import Path
from typing import Any

import nove
import pytest
import stim

from noise_aware_compilation.circuits import cz_native, repetition_code
from noise_aware_compilation.conversion import TWO_QUBIT_LABELS, convert_fake_backend
from noise_aware_compilation.devices import load_device, stored_hashes
from noise_aware_compilation.placement import enumerate_placements, interaction_edges

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "devices"
CZ_DEVICES = [("fez", "FakeFez"), ("marrakesh", "FakeMarrakesh"), ("miami", "FakeMiami")]
#: Couplers IBM lists with error 1.0 on each device (SPEC_V3.md R3-C1).
REMOVED = {"fez": 7, "marrakesh": 13, "miami": 7}


def _raw(name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    conf = json.loads((RAW / f"conf_{name}.json").read_text())
    props = json.loads((RAW / f"props_{name}.json").read_text())
    return conf, props


def _gates(props: dict[str, Any], gate: str) -> dict[tuple[int, ...], dict[str, float]]:
    return {
        tuple(g["qubits"]): {p["name"]: p["value"] for p in g["parameters"]}
        for g in props["gates"]
        if g["gate"] == gate
    }


# --- T3-C1: CZ conversion --------------------------------------------------------------------


@pytest.mark.parametrize("name", [d for d, _ in CZ_DEVICES])
def test_t3_c1_cz_conversion_follows_every_rule(name: str) -> None:
    conf, props = _raw(name)
    stored = load_device(name)
    snap, device, noise = stored.snapshot, stored.device, stored.noise
    assert not any(g["gate"] == "cx" for g in props["gates"])
    cz = _gates(props, "cz")
    sx = _gates(props, "sx")

    removed = {
        (min(a, b), max(a, b))
        for a, b in conf["coupling_map"]
        if cz[(a, b)]["gate_error"] > 0.8 or cz[(b, a)]["gate_error"] > 0.8
    }
    assert len(removed) == REMOVED[name]
    listed = {(min(a, b), max(a, b)) for a, b in conf["coupling_map"]}
    assert set(device.connectivity) == listed - removed
    assert {tuple(e["pair"]) for e in device.metadata["removed_couplers"]} == removed

    lengths = []
    for a, b in conf["coupling_map"]:
        key = nove.OperationKey("CZ", (a, b))
        assert nove.OperationKey("CX", (a, b)) not in snap.operation_parameters
        if (min(a, b), max(a, b)) in removed:
            assert key not in snap.operation_parameters
            assert noise.gate_channel_for(key) is None
            continue
        e = cz[(a, b)]["gate_error"]
        channel = noise.gate_channel_for(key)
        assert channel is not None and channel.probabilities == (1.25 * e / 15,) * 15
        params = snap.operation_parameters[key]
        assert [params[label] for label in TWO_QUBIT_LABELS] == [1.25 * e / 15] * 15
        lengths.append(cz[(a, b)]["gate_length"])

    for q in range(conf["n_qubits"]):
        h = noise.gate_channel_for(nove.OperationKey("H", (q,)))
        assert h is not None and h.probabilities == (1.5 * sx[(q,)]["gate_error"] / 3,) * 3
        qubit = {e["name"]: e["value"] for e in props["qubits"][q]}
        assert noise.readout_probability_for(q) == qubit["readout_error"]
        assert noise.reset_probability_for(q) == 0.0

    assert device.gate_durations == {
        "CZ": statistics.fmean(lengths),
        "H": statistics.fmean(g["gate_length"] for g in sx.values()),
    }
    readout = [
        {e["name"]: e["value"] for e in props["qubits"][q]}["readout_length"]
        for q in range(conf["n_qubits"])
    ]
    reset = _gates(props, "reset")
    notes = snap.provenance["conversion_notes"]
    if reset:
        assert device.reset_duration == statistics.fmean(g["gate_length"] for g in reset.values())
        assert not any("no reset entry" in n for n in notes)
    else:
        x = _gates(props, "x")
        expected = statistics.fmean(readout) + statistics.fmean(
            g["gate_length"] for g in x.values()
        )
        assert device.reset_duration == expected
        assert sum("no reset entry" in n for n in notes) == 1
    assert sum("CZ pair" in n and "removed" in n for n in notes) == REMOVED[name]


def test_t3_c1_miami_is_the_device_without_a_reset_entry() -> None:
    assert not _gates(_raw("miami")[1], "reset")
    assert _gates(_raw("fez")[1], "reset") and _gates(_raw("marrakesh")[1], "reset")
    # the rule reproduces Fez's published reset length
    fez = load_device("fez").device
    _, props = _raw("fez")
    readout = statistics.fmean(
        {e["name"]: e["value"] for e in props["qubits"][q]}["readout_length"]
        for q in range(len(props["qubits"]))
    )
    x = statistics.fmean(g["gate_length"] for g in _gates(props, "x").values())
    assert readout + x == pytest.approx(fez.reset_duration)


@pytest.mark.parametrize("name", ["jakarta", "hanoi", "algiers", "fez", "marrakesh", "miami"])
def test_stored_devices_match_a_fresh_conversion(name: str) -> None:
    """The CZ generalisation leaves the CX devices' snapshots byte for byte as committed."""

    fresh = convert_fake_backend(RAW / f"conf_{name}.json", RAW / f"props_{name}.json")
    stored = load_device(name)
    hashes = stored_hashes()[name]
    assert fresh.snapshot.sha256 == stored.snapshot.sha256 == hashes["snapshot_sha256"]
    assert fresh.device.sha256 == stored.device.sha256 == hashes["device_sha256"]


HELD_OUT = ("boston", "pittsburgh", "berlin", "kingston", "aachen", "nighthawk")
DEVICE_DATA = ROOT / "src" / "noise_aware_compilation" / "device_data"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def _held_out_files_present() -> list[Path]:
    present = [
        path
        for name in HELD_OUT
        for path in (
            RAW / f"conf_{name}.json",
            RAW / f"props_{name}.json",
            *sorted(DEVICE_DATA.glob(f"{name}_*")),
        )
        if path.exists()
    ]
    present += [DEVICE_DATA / "hashes.json"] if set(HELD_OUT) & set(stored_hashes()) else []
    return present


def test_held_out_devices_are_converted_only_after_f4() -> None:
    """X16 and T3-F1: a held-out device file exists only in a checkout at or after freeze
    step F4 whose held-out config is F4's, made by a commit that descends from F4.

    F4 is the last commit that touches ``configs_v3/held_out.json`` (SPEC_V3.md section 8).
    With no held-out file present there is nothing to check; ``tests/test_v3_freeze.py``
    checks the commits that add held-out files, and ``scripts/freeze_guard.py`` refuses a
    conversion unless F4 carries its annotated tag.
    """

    present = _held_out_files_present()
    if not present:
        return
    try:
        f4 = _git("log", "-1", "--format=%H", "--", "configs_v3/held_out.json")
        unchanged = _git("status", "--porcelain", "--", "configs_v3/held_out.json") == ""
    except (OSError, subprocess.CalledProcessError):
        pytest.fail(f"held-out device files present outside a git checkout: {present}")
    assert f4, f"held-out device files present before F4: {present}"
    assert unchanged, "held-out device files present while the held-out config differs from F4"
    for path in present:
        added = _git("log", "--diff-filter=A", "--format=%H", "--", str(path.relative_to(ROOT)))
        for commit in added.split():
            ancestor = subprocess.run(
                ["git", "merge-base", "--is-ancestor", f4, commit], cwd=ROOT
            ).returncode
            assert commit != f4 and ancestor == 0, (path.name, commit)


def _freeze_guard() -> Any:
    spec = importlib.util.spec_from_file_location(
        "freeze_guard", ROOT / "scripts" / "freeze_guard.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_conversion_guard_refuses_without_the_f4_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """The device scripts' --held-out guard needs F4's annotated tag before any file is read."""

    guard = _freeze_guard()
    real = guard._git

    def no_tags(*args: str) -> str:
        return "" if args[:2] == ("tag", "--points-at") else str(real(*args))

    monkeypatch.setattr(guard, "_git", no_tags)
    with pytest.raises(guard.FreezeOrderError, match="tag"):
        guard.require_after_f4()


# --- T3-C2: CZ circuits ----------------------------------------------------------------------

CODES = [(3, "Z"), (3, "X"), (5, "Z"), (7, "Z")]


def _two_qubit_layers(circuit: stim.Circuit) -> list[list[frozenset[int]]]:
    layers: list[list[frozenset[int]]] = [[]]
    for inst in circuit.flattened():
        if inst.name == "TICK":
            layers.append([])
        elif stim.gate_data(inst.name).is_two_qubit_gate:
            t = [x.value for x in inst.targets_copy()]
            layers[-1].extend(frozenset(t[k : k + 2]) for k in range(0, len(t), 2))
    return [layer for layer in layers if layer]


def _with_uniform_noise(circuit: stim.Circuit, p: float) -> stim.Circuit:
    out = stim.Circuit()
    for inst in circuit.flattened():
        targets = inst.targets_copy()
        if inst.name == "M":
            out.append("X_ERROR", targets, p)
        out.append(inst)
        if inst.name in ("R", "H"):
            out.append("DEPOLARIZE1" if inst.name == "H" else "X_ERROR", targets, p)
        elif inst.name in ("CX", "CZ"):
            out.append("DEPOLARIZE2", targets, p)
    return out


@pytest.mark.parametrize(("distance", "basis"), CODES)
@pytest.mark.parametrize("rounds", [1, 3])
def test_t3_c2_cz_circuits_are_deterministic_and_keep_the_cx_pattern(
    distance: int, basis: str, rounds: int
) -> None:
    cx = repetition_code(distance, rounds, basis, "CX")  # type: ignore[arg-type]
    cz = repetition_code(distance, rounds, basis, "CZ")  # type: ignore[arg-type]
    s_cx, s_cz = cx.to_stim(), cz.to_stim()
    assert {i.name for i in s_cz.flattened()} <= {
        "R", "H", "CZ", "M", "TICK", "DETECTOR", "OBSERVABLE_INCLUDE"
    }  # fmt: skip
    assert "CX" not in {i.name for i in s_cz.flattened()}

    # noiseless: every measurement, detector and the observable are deterministic and 0
    s_cz.detector_error_model()  # raises on a non-deterministic detector or observable
    assert not s_cz.compile_sampler(seed=1).sample(64).any()
    dets, obs = s_cz.compile_detector_sampler(seed=1).sample(64, separate_observables=True)
    assert not dets.any() and not obs.any()
    assert s_cz.num_detectors == s_cx.num_detectors
    assert s_cz.num_measurements == s_cx.num_measurements

    # same couplers in the same order of two-qubit layers, so placements carry over
    assert _two_qubit_layers(s_cz) == _two_qubit_layers(s_cx)
    assert interaction_edges(cz) == interaction_edges(cx)

    # the code still corrects: under uniform noise the shortest undetected logical error
    # has the code's distance
    shortest = _with_uniform_noise(s_cz, 1e-3).search_for_undetectable_logical_errors(
        dont_explore_detection_event_sets_with_size_above=4,
        dont_explore_edges_with_degree_above=4,
        dont_explore_edges_increasing_symptom_degree=False,
    )
    assert len(shortest) == distance


def test_t3_c2_bit_flip_round_is_h_cz_cz_h_on_the_ancillas() -> None:
    rounds = repetition_code(3, 2, "Z", "CZ").to_stim()
    text = str(rounds)
    assert "H 1 3\nTICK\nCZ 0 1 2 3\nTICK\nCZ 2 1 4 3\nTICK\nH 1 3\nTICK\nM 1 3" in text


def test_t3_c2_phase_flip_data_idle_in_the_x_basis() -> None:
    """Data H gates around an ancilla's measurement are not cancelled, so B differs from A."""

    a = repetition_code(3, 3, "Z", "CZ").to_stim()
    b = repetition_code(3, 3, "X", "CZ").to_stim()
    assert str(a) != str(b)
    h_data = sum(
        1
        for inst in b.flattened()
        if inst.name == "H"
        for t in inst.targets_copy()
        if t.value % 2 == 0
    )
    assert h_data > 2 * 3  # more than the preparation and final rotation


def test_cz_native_rejects_a_mixed_cx_layer() -> None:
    bad = stim.Circuit("R 0 1 2\nTICK\nCX 0 1\nH 2\nTICK\nM 0 1 2")
    with pytest.raises(ValueError, match="only CX"):
        cz_native(bad)


@pytest.mark.parametrize("name", [d for d, _ in CZ_DEVICES])
def test_cz_circuits_have_placements_on_every_cz_device(name: str) -> None:
    device = load_device(name).device
    for distance, basis in CODES:
        if name == "miami" and distance == 7:
            continue  # 8.5 million placements, about 2.5 minutes to enumerate
        cz = repetition_code(distance, 3, basis, "CZ")  # type: ignore[arg-type]
        cx = repetition_code(distance, 3, basis, "CX")  # type: ignore[arg-type]
        placements = enumerate_placements(cz, device)
        assert placements and placements == enumerate_placements(cx, device)


# --- T3-C3: CZ Target ------------------------------------------------------------------------


@pytest.mark.parametrize(("device", "fake"), CZ_DEVICES)
def test_t3_c3_native_target_equals_the_fake_backends_target(device: str, fake: str) -> None:
    pytest.importorskip("qiskit")
    runtime = pytest.importorskip("qiskit_ibm_runtime.fake_provider")
    from nac_bench.baselines_v3 import native_target

    stored = load_device(device)
    ours = native_target(stored.snapshot, stored.device)
    theirs = getattr(runtime, fake)().target
    assert "cz" in ours.operation_names and "cx" not in ours.operation_names
    checked = 0
    for op in ours.operation_names:
        if op == "reset" and op not in theirs.operation_names:
            continue  # FakeMiami's Target has no reset; ours has one without an error
        for qargs, props in ours[op].items():
            other = theirs[op][qargs]
            assert (props.error is None) == (other.error is None), (op, qargs)
            if props.error is not None:
                assert props.error == other.error, (op, qargs)
            checked += 1
    removed = {tuple(c["pair"]) for c in stored.device.metadata["removed_couplers"]}
    missing = {tuple(sorted(q)) for q in theirs["cz"] if q not in ours["cz"]}
    assert missing == removed
    assert checked > 0
    readout = native_target(stored.snapshot, stored.device, "readout")
    for q in range(len(stored.snapshot.qubit_parameters)):
        assert readout["reset"][(q,)].error == readout["measure"][(q,)].error


def test_t3_c3_vf2_target_carries_cz_at_the_channel_total() -> None:
    pytest.importorskip("qiskit")
    from nac_bench.qiskit_layout import build_target, channel_total, qiskit_layout, two_qubit_gate

    stored = load_device("fez")
    assert two_qubit_gate(stored.device) == "CZ"
    assert two_qubit_gate(load_device("hanoi").device) == "CX"
    target = build_target(stored.device, stored.noise)
    assert "cz" in target.operation_names and "cx" not in target.operation_names
    for (a, b), props in target["cz"].items():
        assert props.error == channel_total(stored.noise, "CZ", (a, b)) > 0
    circuit = repetition_code(3, 3, "Z", "CZ")
    choice = qiskit_layout(circuit, stored.device, stored.noise)
    assert choice.failure is None


def test_t3_c3_qiskit_default_and_mapomatic_run_on_a_cz_device() -> None:
    pytest.importorskip("mapomatic")
    import warnings

    from nac_bench.baselines_v3 import (
        SnapshotBackend,
        mapomatic_choice,
        native_target,
        qiskit_default,
    )
    from nac_bench.qiskit_layout import qiskit_circuit

    stored = load_device("fez")
    circuit = repetition_code(3, 3, "X", "CZ")
    qc = qiskit_circuit(circuit)
    assert qc.count_ops().get("cz") == 12 and "cx" not in qc.count_ops()
    edges = sorted(interaction_edges(circuit))
    candidates = set(enumerate_placements(circuit, stored.device))
    target = native_target(stored.snapshot, stored.device, "readout")
    default = qiskit_default(qc, target, 1, 20261021, edges, candidates)
    assert default.failure is None and default.swap_free and default.mapping in candidates
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mapo = mapomatic_choice(qc, SnapshotBackend("fez", target), 20261021, edges, candidates)
    assert mapo.failure is None and mapo.mapping in candidates
    assert mapo.num_layouts == len(candidates)
