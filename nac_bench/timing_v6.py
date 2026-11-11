"""Revision 6's cost projection, from development timings only (SPEC_V3.md R3-C7).

Usage::

    python -m nac_bench.timing_v6 [--out results_v3/timing_pilot/projection_v6.json]

Revision 6 adds work that revision 5's projection (``projection_v5.json``) did not cover:
every placement simulated on FakeBerlin at d=5, a larger best-found set on the other
square-lattice cases (1,000 uniform placements and six top-30 lists), the d=3 surface code,
the directional setting on nine cases, the isolation references' choices at the family's
shots, the grading variants, the detection rate on every placement and the Qiskit level 2
seed sweep. As in ``timing_v5``, each new cost is a ratio measured here, in one process,
times a cost the second timing pilot measured on patty (``timing_pilot_2.json``), and the
devices measured are development devices only (X16): FakeFez for the heavy-hex repetition
codes, FakeMiami for the square lattice and, with its removed couplers restored (R3-S3),
for the surface code. FakeMiami's d=5 repetition P_L gives a second, square-lattice estimate
of FakeBerlin D5's shots; R3-C5's every-placement guard bounds that case whatever its P_L.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np

from nac_bench.detection_rate import detection_rate
from nac_bench.run_reset import noise_for
from nac_bench.sensitivity_v3 import CorrelatedRedecode
from nac_bench.surface_check import restored
from nac_bench.timing_v5 import (
    FAMILY,
    OTHER,
    PILOT,
    PILOT_SHOTS,
    SETTINGS,
    WORKERS,
    _p_l,
    _per_shot,
    shots_per_seed,
)
from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.decoder_graph import matching_graph
from noise_aware_compilation.devices import load_device
from noise_aware_compilation.evaluation import PlacementEvaluator
from noise_aware_compilation.fast_lowering import Lowering, v1_score_from_dem
from noise_aware_compilation.placement import approximate_dem, enumerate_placements
from noise_aware_compilation.scaled_score import ScaledParameters, scaled_score_from_graph
from noise_aware_compilation.surface_code import (
    DEFAULT_SCHEDULE,
    hook_safe_schedules,
    surface_code,
)

ROOT = Path(__file__).resolve().parent.parent
V5 = ROOT / "results_v3" / "timing_pilot" / "projection_v5.json"
CONFIG = ROOT / "configs_v3" / "held_out.json"
SCHEMA = "noise-aware-compilation.timing-v6/1"
SEED = 20261111
#: R3-C5's guard on an every-placement square-lattice case: its main stage, projected from
#: its own pilot stage, must fit in this many wall hours (one setting), else the case falls
#: back to the best-found union
EVERY_PLACEMENT_LIMIT_HOURS = 24.0  # configs_v3/held_out.json best_found
#: an upper bound on one Qiskit level 2 transpile: the second pilot's whole level 1 to 3 and
#: mapomatic time on FakeFez d=7
QISKIT_L2_BOUND_SECONDS = 8.0
CONFIRMATORY = ("boston", "pittsburgh", "berlin")
FAMILY_DEVICES = ("boston", "pittsburgh", "berlin", "nighthawk")
ALL_DEVICES = ("boston", "pittsburgh", "kingston", "aachen", "berlin", "nighthawk")
HEAVY_HEX = ("boston", "pittsburgh", "kingston", "aachen")
#: FakeBoston and FakePittsburgh share FakeKingston's coupling map (section 1)
COUPLING_MAP = {"boston": "kingston", "pittsburgh": "kingston"}
DISTANCE = {"A": 3, "B": 3, "D5": 5, "D7": 7}


def measure() -> dict[str, Any]:
    out: dict[str, Any] = {}
    rng = np.random.default_rng(SEED)
    fez = load_device("fez")
    noise = noise_for("readout", fez)

    # the detection rate against the v1 score, both from the same undecomposed DEM
    for device in ("fez", "miami"):
        stored = load_device(device)
        model = noise_for("readout", stored)
        circuit = repetition_code(7, 3, "Z", "CZ")
        placements = enumerate_placements(circuit, stored.device)
        sample = [placements[int(i)] for i in rng.choice(len(placements), 200, replace=False)]
        lowering = Lowering(circuit, stored.device, model)
        dems = [approximate_dem(lowering.stim_circuit(m), decompose=False) for m in sample]
        t = time.perf_counter()
        for dem in dems:
            v1_score_from_dem(dem)
        v1 = time.perf_counter() - t
        t = time.perf_counter()
        for dem in dems:
            detection_rate(dem)
        out[f"{device}-7-detection-over-v1"] = (time.perf_counter() - t) / v1

    # FakeMiami's d=5 repetition P_L: a square-lattice estimate of FakeBerlin D5's shots
    stored = load_device("miami")
    model = noise_for("readout", stored)
    circuit = repetition_code(5, 3, "Z", "CZ")
    placements = enumerate_placements(circuit, stored.device)
    sample = [placements[int(i)] for i in rng.choice(len(placements), 100, replace=False)]
    pilot = []
    for k, m in enumerate(sample):
        counts = PlacementEvaluator(circuit, stored.device, model, m).run(
            PILOT_SHOTS, [SEED + 1000 + k]
        )
        pilot.append(counts.logical_failures / counts.shots)
    out["miami-5-pilot-p"] = pilot

    # the d=3 surface code on FakeMiami with its couplers restored (R3-S3), 3 rounds
    base = PlacementEvaluator(
        repetition_code(7, 3, "Z", "CZ"),
        fez.device,
        noise,
        enumerate_placements(repetition_code(7, 3, "Z", "CZ"), fez.device)[0],
    )
    fez7 = _per_shot(base.run_chunk)
    out["fez-7-seconds-per-shot-here"] = fez7
    lattice, surface_model = restored("miami", "readout")
    surface = surface_code(3, 3, DEFAULT_SCHEDULE, "CZ")
    mapping = enumerate_placements(surface, lattice)[0]
    evaluator = PlacementEvaluator(surface, lattice, surface_model, mapping)
    s3 = _per_shot(evaluator.run_chunk)
    out["surface-3-over-fez-7"] = s3 / fez7
    out["surface-3-redecode-over-main"] = _per_shot(CorrelatedRedecode(evaluator).run_chunk) / s3
    out["surface-3-p_l"] = _p_l(evaluator, 500_000)
    lowering = Lowering(surface, lattice, surface_model)
    noisy = lowering.stim_circuit(mapping)
    graph = matching_graph(approximate_dem(noisy, decompose=True))
    for k in (10, 80, 640):
        t = time.perf_counter()
        scaled_score_from_graph(graph, ScaledParameters(k, 0.05))
        out[f"surface-3-scaled-k{k}-seconds-here"] = time.perf_counter() - t
    t = time.perf_counter()
    detection_rate(approximate_dem(noisy, decompose=False))
    out["surface-3-detection-seconds-here"] = time.perf_counter() - t
    out["surface-3-candidates-miami-restored"] = sum(
        len(enumerate_placements(surface_code(3, 3, schedule, "CZ"), lattice))
        for schedule in hook_safe_schedules(3, 3, "CZ")
    )
    return out


def _variants(config: dict[str, Any], kind: str, basis: str | None) -> int:
    n = 0
    for info in config["grading_variants"]["variants"].values():
        if kind not in info["kinds"]:
            continue
        if basis is not None and "bases" in info and basis not in info["bases"]:
            continue
        n += int(info.get("draws", 1)) if "delta" in info else 1
    return n


def project(
    ratios: dict[str, Any], v5: dict[str, Any], pilot: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    """Revision 6's added work on patty, in core-hours, on top of revision 5's projection."""

    r5 = v5["ratios"]
    measured = pilot["measured"]
    per_shot = float(v5["projection"]["per_shot_patty_fez_d7"])  # patty, FakeFez d=7
    build = float(measured["fez-7-Z"]["seconds_build"]["median"])  # patty, one evaluator
    rows = {c["case"]: c for c in pilot["projection"]["cases"]}
    counts = pilot["held_out_placement_counts"]
    lines: list[dict[str, Any]] = []

    def add(item: str, core_hours: float, note: str) -> None:
        lines.append({"item": item, "core_hours": core_hours, "note": note})

    def cost(distance: int) -> float:  # patty seconds per shot, 3 rounds
        here = r5[f"fez-{distance}-seconds-per-shot-here"] / r5["fez-7-seconds-per-shot-here"]
        return per_shot * float(here)

    def p_fez(distance: int) -> float:
        return float(r5[f"fez-{distance}-p_l-3-rounds"])

    def placement_seconds(distance: int, p: float, tier: tuple[int, int]) -> float:
        """One simulated placement: pilot, three main-stage seeds and four evaluator builds."""

        return 4 * build + (PILOT_SHOTS + 3 * shots_per_seed(p, *tier)) * cost(distance)

    def per_placement_pilot2(case: str) -> float:  # patty's projected simulation, seconds
        r = rows[case]
        return 3600 * float(r["simulation_core_hours"]) / int(r["simulated_placements"])

    def placements(device: str, label: str) -> int:
        return int(counts[f"{COUPLING_MAP.get(device, device)}-{label}"]["placements"])

    summed_s = {
        d: float(measured[f"{d}-7-Z"]["seconds_summed"]["median"]) for d in ("fez", "miami")
    }
    v1_s = {d: float(measured[f"{d}-7-Z"]["seconds_v1"]["median"]) for d in ("fez", "miami")}
    top = int(config["best_found"]["top"])
    random_n = int(config["best_found"]["random"])
    sweep = len(config["qiskit_seed_sweep"]["seeds"])
    methods = 12  # every method's choice, an upper bound

    # 1. FakeBerlin D5: every placement, both settings
    fez5 = per_placement_pilot2("berlin-D5")
    miami = [float(p) for p in ratios["miami-5-pilot-p"]]
    miami5 = float(np.mean([placement_seconds(5, p, OTHER) for p in miami]))
    n_berlin5 = placements("berlin", "D5")
    extra = n_berlin5 - rows["berlin-D5"]["simulated_placements"]
    berlin5 = {
        "fez_p_l": SETTINGS * extra * fez5 / 3600,
        "miami_p_l": SETTINGS * extra * miami5 / 3600,
    }
    add(
        "FakeBerlin D5: every placement simulated",
        max(berlin5.values()),
        f"{n_berlin5} placements (upper bound), the larger of FakeFez's and FakeMiami's P_L "
        f"({berlin5['fez_p_l']:.0f} and {berlin5['miami_p_l']:.0f} core-hours); R3-C5's guard "
        f"caps each setting at {EVERY_PLACEMENT_LIMIT_HOURS:g} wall hours",
    )

    # 2. the other square-lattice cases: 1,000 uniform placements and six top-30 lists
    size = random_n + top * len(config["best_found"]["top_by"]) + methods + sweep
    square = 0.0
    for case in ("berlin-D7", "nighthawk-D5", "nighthawk-D7"):
        r = rows[case]
        square += SETTINGS * (size - r["simulated_placements"]) * per_placement_pilot2(case)
    add(
        "square-lattice best-found set, D7 and FakeNighthawk D5",
        square / 3600,
        f"at most {size} placements per case, up from 198",
    )

    # 3. the d=3 surface code on both square-lattice devices
    s3_cost = per_shot * float(ratios["surface-3-over-fez-7"])
    p3 = float(ratios["surface-3-p_l"])
    candidates = int(ratios["surface-3-candidates-miami-restored"])
    s3_sim = (
        2
        * SETTINGS
        * candidates
        * (4 * build + (PILOT_SHOTS + 3 * shots_per_seed(p3, *OTHER)) * s3_cost)
    )
    family_s3 = (
        2 * SETTINGS * 6 * 3 * (shots_per_seed(p3, *FAMILY) - shots_per_seed(p3, *OTHER)) * s3_cost
    )
    redecode_s3 = (
        6
        * 3
        * shots_per_seed(p3, *FAMILY)
        * s3_cost
        * float(ratios["surface-3-redecode-over-main"])
    )
    scoring_s3 = (
        2
        * SETTINGS
        * candidates
        * (
            sum(float(ratios[f"surface-3-scaled-k{k}-seconds-here"]) for k in (10, 80, 640))
            + float(ratios["surface-3-detection-seconds-here"])
        )
    )
    add(
        "d=3 surface code (S3), FakeBerlin and FakeNighthawk",
        (s3_sim + family_s3 + redecode_s3 + scoring_s3) / 3600,
        f"{candidates} candidates per device (FakeMiami restored's count, an estimate); "
        "family placements at the family's shots; FakeBerlin's re-decoded",
    )

    # 4. the directional setting: nine cases selected again, every choice simulated
    directional = 0.0
    scope = config["setting_scope"]["directional"]
    for device in scope["devices"]:
        proxy = "fez" if device in HEAVY_HEX else "miami"
        for label in scope["circuits"]:
            r = rows[f"{COUPLING_MAP.get(device, device)}-{label}"]
            n = placements(device, label)
            directional += 3600 * r["selection_core_hours"]
            directional += n * summed_s[proxy] * float(r5[f"{proxy}-7-idle-aware-over-summed"])
            directional += n * v1_s[proxy] * float(ratios[f"{proxy}-7-detection-over-v1"])
            d, p = DISTANCE[label], p_fez(DISTANCE[label])
            simulated = 2 * (methods + sweep)  # this setting's and the headline setting's
            directional += simulated * placement_seconds(d, p, OTHER)
            directional += (
                14 * 3 * (shots_per_seed(p, *FAMILY) - shots_per_seed(p, *OTHER)) * cost(d)
            )
    add(
        "directional setting (R3-C13), nine cases",
        directional / 3600,
        "selection again on every placement; every choice here and at the headline setting "
        "simulated, the family placements of both at the family's shots",
    )

    # 5. the isolation references' choices at the family's shots, and their sensitivity runs
    isolation = 0.0
    for distance in DISTANCE.values():
        p = p_fez(distance)
        extra_shots = 3 * (shots_per_seed(p, *FAMILY) - shots_per_seed(p, *OTHER))
        isolation += SETTINGS * len(FAMILY_DEVICES) * 2 * extra_shots * cost(distance)
        n = 3 * shots_per_seed(p, *FAMILY)
        redecode = n * cost(distance) * float(r5[f"fez-{distance}-redecode-over-main"])
        isolation += len(CONFIRMATORY) * 2 * redecode
        if distance > 3:
            pd = float(r5[f"fez-{distance}-p_l-d-rounds"])
            nd = PILOT_SHOTS + 3 * shots_per_seed(pd, *FAMILY)
            longer = nd * cost(distance) * float(r5[f"fez-{distance}-d-rounds-over-3-rounds"])
            isolation += len(CONFIRMATORY) * 2 * longer
    p5 = float(r5["surface-5-p_l"])
    s5_cost = per_shot * float(r5["surface-5-over-fez-7"])
    isolation += (
        SETTINGS * 2 * 3 * (shots_per_seed(p5, *FAMILY) - shots_per_seed(p5, *OTHER)) * s5_cost
    )
    isolation += (
        3 * shots_per_seed(p5, *FAMILY) * s5_cost * float(r5["surface-5-redecode-over-main"])
    )
    add(
        "isolation references' choices at the family's shots",
        isolation / 3600,
        "two more family placements per repetition case, one per surface case, with their "
        "re-decode and d-round memory",
    )

    # 6. the grading variants on every confirmatory family placement, at the other tier
    variants = 0.0
    for _device in CONFIRMATORY:
        for label, distance in DISTANCE.items():
            basis = "X" if label == "B" else "Z"
            k = _variants(config, "repetition", basis)
            p = p_fez(distance)
            variants += k * 7 * (3 * build + 3 * shots_per_seed(p, *OTHER) * cost(distance))
    k5 = _variants(config, "surface", None)
    variants += k5 * 6 * (3 * build + 3 * shots_per_seed(p5, *OTHER) * s5_cost)
    k3 = k5
    variants += k3 * 6 * (3 * build + 3 * shots_per_seed(p3, *OTHER) * s3_cost)
    add(
        "grading variants (R3-C15)",
        variants / 3600,
        "every variant on at most 7 family placements per confirmatory case, 3 seeds, "
        "other placements' shots",
    )

    # 7. the detection rate on every placement, both settings
    rate = 0.0
    for device in ALL_DEVICES:
        proxy = "fez" if device in HEAVY_HEX else "miami"
        for label in DISTANCE:
            n = placements(device, label)
            rate += SETTINGS * n * v1_s[proxy] * float(ratios[f"{proxy}-7-detection-over-v1"])
    add("detection rate on every placement (R3-C16)", rate / 3600, "ratio to the v1 score")

    # 8. the Qiskit level 2 seed sweep
    cases = 0
    for device, info in config["devices"].items():
        for label in info["circuits"]:
            in_scope = device in config["setting_scope"]["directional"]["devices"] and (
                label in config["setting_scope"]["directional"]["circuits"]
            )
            cases += 2 + int(in_scope)
    add(
        "Qiskit level 2 seed sweep (R3-C14)",
        cases * sweep * QISKIT_L2_BOUND_SECONDS / 3600,
        f"{cases} cases x {sweep} transpiles, each bounded by {QISKIT_L2_BOUND_SECONDS:g} s; "
        "its choices are simulated with the best-found set or every placement",
    )

    added = math.fsum(line["core_hours"] for line in lines)
    base = float(v5["projection"]["total_core_hours"])
    total = base + added
    n7 = placements("berlin", "D7")
    berlin_d7 = (
        rows["berlin-D7"]["selection_core_hours"]
        + n7
        * (
            summed_s["miami"] * float(r5["miami-7-idle-aware-over-summed"])
            + v1_s["miami"] * float(ratios["miami-7-detection-over-v1"])
        )
        / 3600
    )
    berlin_d5 = max(berlin5.values()) / SETTINGS
    return {
        "per_shot_patty_fez_d7": per_shot,
        "revision_5_total_core_hours": base,
        "lines": lines,
        "added_core_hours": added,
        "total_core_hours": total,
        "total_wall_hours_on_48_workers": total / WORKERS,
        "largest_single_case": {
            "case": "berlin-D7 and nighthawk-D7 selection, one setting, with the idle-aware "
            "summed error and the detection rate",
            "wall_hours": berlin_d7 / WORKERS,
            "limit_hours": 24,
        },
        "berlin_d5_main_stage": {
            "wall_hours_per_setting": berlin_d5 / WORKERS,
            "limit_hours": EVERY_PLACEMENT_LIMIT_HOURS,
        },
        "notes": [
            "development timings only: patty's second pilot, times ratios measured here on "
            "FakeFez and FakeMiami (X16), and revision 5's ratios (projection_v5.json)",
            "placement counts are coupling-map upper bounds; P_L from FakeFez (repetition), "
            "FakeMiami (FakeBerlin D5's second estimate) and FakeMiami restored (surface)",
            "FakeBerlin D7 at the directional setting is a third selection of 12.8M "
            "placements, so FakeBerlin's three D7 selections are three cases of about 7.6 "
            "wall hours each",
            "the scaled score's and the detection rate's seconds are this machine's, not "
            "scaled to patty",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", type=Path, default=ROOT / "results_v3" / "timing_pilot" / "projection_v6.json"
    )
    parser.add_argument("--ratios", type=Path, help="reuse an earlier output's ratios")
    args = parser.parse_args(argv)
    pilot = json.loads(PILOT.read_text(encoding="utf-8"))
    v5 = json.loads(V5.read_text(encoding="utf-8"))
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if args.ratios is not None:
        ratios = json.loads(args.ratios.read_text(encoding="utf-8"))["ratios"]
    else:
        ratios = measure()
    result = {
        "schema": SCHEMA,
        "description": __doc__,
        "ratios": ratios,
        "projection": project(ratios, v5, pilot, config),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result["projection"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
