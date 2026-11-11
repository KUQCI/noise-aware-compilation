"""T3-F3: every part of v3 agrees on SPEC_V3.md revision 6, and the older documents carry it.

The spec, the held-out config, the score freeze, the F3 record, the runner, the analysis and
the tests must name the same revision, devices and roles, references, family sizes, *k*,
sensitivity *k*, shots tiers and claim thresholds. Any disagreement fails here, before a
held-out device is converted. Revision 6 adds its settings, simulated sets, exploratory rows
and claim wording. The last tests check revision 5's presentation edits (R1, R2, R9, R10 of
``REVIEW_PRE_F4.md``) so that they cannot quietly come undone.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from nac_bench import analysis_v3, report_v2, run_v3
from noise_aware_compilation import report as report_v1

ROOT = Path(__file__).resolve().parent.parent
SPEC = (ROOT / "SPEC_V3.md").read_text(encoding="utf-8")
CONFIG: dict[str, Any] = json.loads(
    (ROOT / "configs_v3" / "held_out.json").read_text(encoding="utf-8")
)
FREEZE: dict[str, Any] = json.loads(
    (ROOT / "configs_v3" / "score_freeze.json").read_text(encoding="utf-8")
)
F3: dict[str, Any] = json.loads((ROOT / "configs_v3" / "freeze_f3.json").read_text("utf-8"))
REVISION = 6
FAKE = {
    "boston": "FakeBoston",
    "pittsburgh": "FakePittsburgh",
    "berlin": "FakeBerlin",
    "kingston": "FakeKingston",
    "aachen": "FakeAachen",
    "nighthawk": "FakeNighthawk",
}
ROLES = {
    "boston": "confirmatory",
    "pittsburgh": "confirmatory",
    "berlin": "confirmatory",
    "kingston": "seen",
    "aachen": "seen",
    "nighthawk": "synthetic",
}


def _row(key: str) -> str:
    """The spec's table row whose first cell is ``key``."""

    rows = [line for line in SPEC.splitlines() if line.startswith(f"| {key} |")]
    assert len(rows) == 1, key
    return rows[0]


def test_every_part_names_the_revision() -> None:
    revisions = [int(n) for n in re.findall(r"^\*\*Revision (\d+) \(", SPEC, re.MULTILINE)]
    assert max(revisions) == REVISION
    assert CONFIG["spec_revision"] == REVISION
    assert run_v3.SPEC_REVISION == REVISION
    assert analysis_v3.SPEC_REVISION == REVISION


def test_devices_and_roles_agree() -> None:
    roles = {device: info["role"] for device, info in CONFIG["devices"].items()}
    assert roles == ROLES
    assert set(roles.values()) <= set(run_v3.ROLES)
    # section 3 names the same roles
    devices = _row("Held-out devices")
    assert "**Confirmatory, heavy-hex:** FakeBoston and FakePittsburgh" in devices
    assert "**Confirmatory, square lattice:** FakeBerlin" in devices
    assert "**Seen:** FakeKingston" in devices and "FakeAachen" in devices
    assert "**Synthetic and seen:** FakeNighthawk" in devices
    # X16 binds every held-out device, and the not-converted test covers them all
    x16 = _row("X16")
    assert all(name in x16 for name in FAKE.values())
    cz_test = (ROOT / "tests" / "test_v3_cz.py").read_text(encoding="utf-8")
    assert all(f'"{device}"' in cz_test for device in FAKE)


def test_claim_layouts_are_the_confirmatory_devices() -> None:
    layouts = CONFIG["claim_rules"]["layouts"]
    confirmatory = {d for d, role in ROLES.items() if role == "confirmatory"}
    assert {d for layout in layouts.values() for d in layout["devices"]} == confirmatory
    for lattice, layout in layouts.items():
        assert all(CONFIG["devices"][d]["lattice"] == lattice for d in layout["devices"])
        repetition = [
            (d, label)
            for d in layout["devices"]
            for label in CONFIG["devices"][d]["circuits"]
            if CONFIG["circuits"][label]["kind"] == "repetition"
        ]
        assert layout["repetition_cases"] == len(repetition)
        assert layout["t"] == layout["repetition_cases"] // 2 + 1  # section 7's derivation
    claim_rules = SPEC[SPEC.index("**Claim rules**") :]
    assert "FakeBoston and FakePittsburgh, with 8 repetition cases and *t* = 5" in claim_rules


def test_references_and_family_sizes() -> None:
    headline = CONFIG["headline_setting"]
    headline_references: dict[str, Sequence[str]] = {
        kind: list(CONFIG["headline_references"]) for kind in ("repetition", "surface")
    }
    family = analysis_v3.planned(CONFIG, headline, headline_references)
    isolation = analysis_v3.planned(CONFIG, headline, CONFIG["isolation_references"])
    assert len(family) == 52 and len(isolation) == 25
    assert "**Family `v3-headline`, 52 comparisons" in SPEC
    assert "**Family `v3-decoder-graph-isolation`, 25 comparisons" in SPEC
    assert "52 members" in _row("T3-F2") and "isolation family 25" in _row("T3-F2")
    assert CONFIG["headline_references"] == [
        "naive",
        "lowest-summed-error",
        "qiskit-default-l2",
        "mapomatic",
    ]
    assert set(CONFIG["isolation_references"]["repetition"]) == {
        "idle-aware-summed-error",
        "v1",
    }


def test_revision_6_settings_and_sets_agree() -> None:
    confirmatory = sorted(d for d, role in ROLES.items() if role == "confirmatory")
    scope = CONFIG["setting_scope"]["directional"]
    assert sorted(scope["devices"]) == confirmatory and scope["circuits"] == ["A", "D5", "D7"]
    assert CONFIG["settings_order"] == ["readout", "zero", "directional"]
    assert "A, D5 and D7 of the confirmatory devices" in _row("R3-C13")
    best = CONFIG["best_found"]
    assert (best["random"], best["top"], best["every_placement_cases"]) == (1000, 30, ["berlin-D5"])
    assert best["every_placement_limit_hours"] == 24
    assert "1,000 valid placements" in _row("R3-C5") and "24 wall hours" in _row("R3-C5")
    assert CONFIG["circuits"]["S3"] == {
        "kind": "surface",
        "distance": 3,
        "rounds": 3,
        "family": False,
    }
    assert CONFIG["qiskit_seed_sweep"]["seeds"] == list(range(20261207, 20261217))
    assert "20261207 to 20261216" in _row("R3-C14")
    variants = set(CONFIG["grading_variants"]["variants"])
    assert variants == {
        "cz-duration-min",
        "cz-duration-max",
        "idle-z-x2",
        "cz-without-1q",
        "lognormal-0.1",
        "lognormal-0.25",
        "directional",
    }
    assert "`cz-without-1q`" in _row("K19")
    assert "berlin" in CONFIG["claim_rules"]["device_notes"]


def test_layout_claims_never_say_no_difference_shown() -> None:
    """Revision 6: a layout below t says how many cases were better and makes no claim."""

    wording = analysis_v3.layout_claim("naive", ["a"], [], 5, "on D", 8)
    assert wording == (
        "better after Holm in 1 of 8 cases (fewer than t = 5); no layout claim against naive on D"
    )
    table = SPEC[SPEC.index("**Claim rules**") : SPEC.index("**Secondary endpoints")]
    rows = [line for line in table.splitlines() if line.startswith('| "')]
    assert not [r for r in rows if r.startswith('| "no difference shown')]
    assert "no layout claim" in CONFIG["claim_rules"]["description"]


def test_surface_k_agrees_everywhere() -> None:
    surface = {"k": 640, "step": 0.05}
    assert CONFIG["surface_scaled_score"] == surface
    assert FREEZE["surface_scaled_score"] == surface
    assert CONFIG["surface_k_sensitivity"] == [10, 80]
    second = F3["commits"][1]
    assert second["tag"] == "v3-score-freeze-surface-k"
    assert second["parameters"] == ["surface_scaled_score"]
    assert F3["commits"][0]["tag"] == "v3-score-freeze"
    f3 = _row("F3")
    assert "`v3-score-freeze`" in f3 and "`v3-score-freeze-surface-k`" in f3 and "640" in f3
    assert "*k* = 10 and 80" in SPEC
    assert "surface-code *k*" in _row("X18")


def test_shots_tiers_agree() -> None:
    rule = CONFIG["shots_rule"]
    assert rule["family"] == {"expected_failures": 4000, "max_shots_per_seed": 102_400_000}
    assert rule["other"] == {"expected_failures": 400, "max_shots_per_seed": 25_600_000}
    assert rule["pilot_shots"] == 100_000 and rule["min_shots_per_seed"] == 200_000
    shots = _row("R3-C6")
    for text in ("*F* = 4,000", "*C* = 102,400,000", "*F* = 400", "*C* = 25,600,000"):
        assert text in shots, text
    family, other = run_v3.stage_rule(rule, True), run_v3.stage_rule(rule, False)
    assert (family["expected_failures"], family["max_shots_per_seed"]) == (4000, 102_400_000)
    assert (other["expected_failures"], other["max_shots_per_seed"]) == (400, 25_600_000)


def test_banners_point_older_documents_to_v3() -> None:
    """R1: README, REPORT.md and REPORT_V2.md say what v3 changed, from their generators."""

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    v1 = (ROOT / "REPORT.md").read_text(encoding="utf-8")
    v2 = (ROOT / "REPORT_V2.md").read_text(encoding="utf-8")
    for text in (readme, v1, v2):
        assert text.count("<!-- v3-banner:start -->") == 1
    assert "\n".join(report_v1.V3_BANNER) in v1
    assert "\n".join(report_v2.V3_BANNER) in v2
    assert "4 of 4" in readme and "1 of 4" in readme
    assert "\N{MINUS SIGN}40.5%" in v2 and "9 of 10" in v2 and "\N{MINUS SIGN}0.016" in v2
    assert "+6.0%" in v1 and "+10.5%" in v1


def test_retired_wording_stays_out() -> None:
    """R2 and R9: no "matches or beats Qiskit", and couplers at error 1.0 are "uncalibrated"."""

    docs = [
        ROOT / name
        for name in (
            "README.md",
            "REPORT.md",
            "REPORT_V2.md",
            "SPEC.md",
            "SPEC_V2.md",
            "SPEC_V3.md",
            "DECISIONS.md",
            "DECISIONS_V2.md",
        )
    ]
    docs += sorted((ROOT / "results_v3").rglob("*.md"))
    for path in docs:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"match(es)? or beats?", text, re.IGNORECASE), path.name
        assert not re.search(r"\b(dead|died)\b", text, re.IGNORECASE), path.name


def test_readme_installs_from_main_and_links_are_public() -> None:
    """R10: the install line names `main`, and no document links a private claude.ai page."""

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    installs = re.findall(r"git\+https://github\.com/\S+?@([\w./-]+)\"", readme)
    assert installs == ["main"]
    assert "(binding; not edited)" not in readme
    for path in ROOT.glob("*.md"):
        assert "claude.ai/code/artifact" not in path.read_text(encoding="utf-8"), path.name
