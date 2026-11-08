"""The held-out analysis on synthetic run files (T3-F2, section 7's claim rules)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nac_bench import analysis_v3

REFERENCES = ["naive", "lowest-summed-error", "qiskit-default-l1", "mapomatic"]


def _config(devices: dict[str, Any]) -> dict[str, Any]:
    return {
        "_config_sha256": "synthetic",
        "family": "v3-headline",
        "headline_setting": "readout",
        "settings_order": ["readout", "zero"],
        "headline_references": REFERENCES,
        "alpha": 0.05,
        "claim_rules": {"beats_min_cases": 5},
        "devices": devices,
        "circuits": {
            "A": {"kind": "repetition"},
            "B": {"kind": "repetition"},
            "D5": {"kind": "repetition"},
            "D7": {"kind": "repetition"},
            "S5": {"kind": "surface"},
        },
    }


def _run(
    case_id: str, device: str, lattice: str, label: str, rates: list[float], picks: dict[str, Any]
) -> dict[str, Any]:
    kind = "surface" if label == "S5" else "repetition"
    shots = 1_000_000
    simulated = [
        {
            "index": i,
            "scores": {"decoder_graph": float(i), "scaled": float(i)},
            "per_seed": [
                {"base_seed": b, "logical_failures": round(rate * shots), "shots": shots}
                for b in (1, 2, 3)
            ],
        }
        for i, rate in enumerate(rates)
    ]
    methods: dict[str, Any] = {}
    for name, index in picks.items():
        if isinstance(index, str):  # a failure message
            methods[name] = {"index": None, "detail": {"failure": index}}
        else:
            methods[name] = {"index": index, "detail": {}}
    return {
        "config_file_sha256": "synthetic",
        "no_valid_placement": False,
        "case": {
            "case_id": case_id,
            "device": device,
            "lattice": lattice,
            "synthetic_calibration": False,
            "setting": case_id.split("-")[1],
            "kind": kind,
            "circuit": {"label": label},
        },
        "simulated": simulated,
        "simulated_set": {"every_placement": True, "random_sample": []},
        "methods": methods,
    }


def _write(tmp_path: Path, runs: list[dict[str, Any]]) -> Path:
    for r in runs:
        (tmp_path / f"{r['case']['case_id']}.json").write_text(json.dumps(r))
    return tmp_path


def test_family_holm_and_claims(tmp_path: Path) -> None:
    devices = {
        "kingston": {"lattice": "heavy-hex", "circuits": ["A", "B", "D5", "D7"]},
        "aachen": {"lattice": "heavy-hex", "circuits": ["A", "B", "D5", "D7"]},
    }
    runs = []
    for device in devices:
        for label in ("A", "B", "D5", "D7"):
            # placement 0: the choice at 1%; 1: naive at 2%; 2: summed error at 1.001%
            picks: dict[str, int | str] = {
                "decoder-graph": 0,
                "naive": 1,
                "lowest-summed-error": 2,
                "qiskit-default-l1": 1,
                "mapomatic": 0,
            }
            if device == "aachen" and label == "D7":
                picks["mapomatic"] = "transpiled circuit is not SWAP-free: two SWAPs"
            runs.append(
                _run(
                    f"v3-readout-{device}-{label}",
                    device,
                    "heavy-hex",
                    label,
                    [0.010, 0.020, 0.01001],
                    picks,
                )
            )
    result = analysis_v3.analyse(_config(devices), _write(tmp_path, runs))
    fam = result["families"]["v3-headline"]
    assert fam["planned_size"] == 32
    assert fam["size"] == 31  # aachen D7's mapomatic left: not SWAP-free
    assert fam["left_the_family"] == [
        {
            "case_id": "v3-readout-aachen-D7",
            "method": "decoder-graph",
            "reference": "mapomatic",
            "reason": "transpiled circuit is not SWAP-free: two SWAPs",
        }
    ]
    by = {(m["case_id"], m["reference"]): m for m in fam["members"]}
    assert by[("v3-readout-kingston-A", "naive")]["after_holm"] == "better"
    assert by[("v3-readout-kingston-A", "mapomatic")]["same_placement"]
    assert by[("v3-readout-kingston-A", "mapomatic")]["after_holm"] == "tie"
    assert by[("v3-readout-kingston-A", "lowest-summed-error")]["after_holm"] == "tie"
    claims = {c["reference"]: c["claim"] for c in result["claims"] if c["scope"] == "layout"}
    assert claims["naive"] == "beats naive on this layout"
    assert claims["qiskit-default-l1"] == "beats qiskit-default-l1 on this layout"
    assert claims["lowest-summed-error"] == "no difference shown against lowest-summed-error"
    assert claims["mapomatic"] == "no difference shown against mapomatic"
    # the zero-setting family has no run files here, so every member is missing
    zero = result["families"]["v3-headline at reset zero (exploratory)"]
    assert zero["size"] == 0 and len(zero["left_the_family"]) == 32


def test_loss_is_named_and_surface_claims(tmp_path: Path) -> None:
    devices = {
        "berlin": {"lattice": "square", "circuits": ["A", "S5"]},
    }
    rep = {
        "decoder-graph": 0,
        "naive": 1,
        "lowest-summed-error": 1,
        "qiskit-default-l1": 1,
        "mapomatic": 1,
    }
    surf = {
        "scaled": 1,
        "naive": 0,
        "lowest-summed-error": 1,
        "qiskit-default-l1": 0,
        "mapomatic": 2,
    }
    runs = [
        # the choice (0) is worse than every reference (1)
        _run("v3-readout-berlin-A", "berlin", "square", "A", [0.03, 0.01], rep),
        _run("v3-readout-berlin-S5", "berlin", "square", "S5", [0.05, 0.03, 0.03], surf),
    ]
    result = analysis_v3.analyse(_config(devices), _write(tmp_path, runs))
    layout = {c["reference"]: c for c in result["claims"] if c["scope"] == "layout"}
    assert layout["naive"]["claim"] == "loses to naive on this layout"
    assert layout["naive"]["worse_after_holm"] == ["v3-readout-berlin-A"]
    surface = {c["reference"]: c["claim"] for c in result["claims"] if c["scope"] != "layout"}
    assert surface["naive"] == "beats naive"
    assert surface["lowest-summed-error"] == "no difference shown against lowest-summed-error"
    assert surface["mapomatic"] == "no difference shown against mapomatic"
    regret = {s["case_id"]: s for s in result["secondary"]}
    assert regret["v3-readout-berlin-A"]["regret"]["decoder-graph"]["verdict"] == "worse"
