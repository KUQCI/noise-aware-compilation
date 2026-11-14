"""Revision 5's cost projection, from development timings only (SPEC_V3.md R3-C7, B7).

Usage::

    python -m nac_bench.timing_v5 [--out results_v3/timing_pilot/projection_v5.json]

Revision 5 adds work that neither timing pilot measured: the idle-aware summed error on every
placement, the surface code's simulation and its scaled score at three *k* values, the
family placements' 4,000-failure main stage, the correlated re-decode and the *d*-round
memory (R3-C10), and two more heavy-hex devices. This module measures each new cost as a
ratio to a cost the second pilot measured on patty (``timing_pilot_2.json``), on the same
machine and in one process, so the machine cancels; the projection is patty's measured
cost times the ratio. It runs on development devices only (X16): FakeFez for the heavy-hex
repetition codes and FakeMiami, with its removed couplers restored (R3-S3), for the surface
code. P_L for the shots rule comes from these development devices too; the placement counts
are the coupling-map upper bounds the pilot recorded.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from nac_bench.run_reset import noise_for
from nac_bench.sensitivity_v3 import CorrelatedRedecode
from nac_bench.surface_check import restored
from noise_aware_compilation.baselines import idle_aware_summed_error, summed_error
from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.decoder_graph import matching_graph
from noise_aware_compilation.devices import load_device
from noise_aware_compilation.evaluation import PlacementEvaluator
from noise_aware_compilation.fast_lowering import Lowering
from noise_aware_compilation.placement import approximate_dem, enumerate_placements
from noise_aware_compilation.scaled_score import ScaledParameters, scaled_score_from_graph
from noise_aware_compilation.surface_code import (
    DEFAULT_SCHEDULE,
    hook_safe_schedules,
    surface_code,
)

ROOT = Path(__file__).resolve().parent.parent
PILOT = ROOT / "results_v3" / "timing_pilot" / "timing_pilot_2.json"
SCHEMA = "noise-aware-compilation.timing-v5/1"
SEED = 20261110
SHOTS = 200_000
WORKERS = 48
SETTINGS = 2
PILOT_SHOTS = 100_000
FAMILY = (4_000, 102_400_000)
OTHER = (400, 25_600_000)
MIN_SHOTS = 200_000
#: confirmatory devices and the cases the sensitivity runs cover
HEAVY_HEX = ("boston", "pittsburgh", "kingston", "aachen")
CONFIRMATORY = ("boston", "pittsburgh", "berlin")
SQUARE = ("berlin", "nighthawk")
#: devices whose placement counts are another device's (same coupling map, read under X16)
COUPLING_MAP = {"boston": "kingston", "pittsburgh": "kingston"}


def _per_shot(run: Callable[[int, int], Any], shots: int = SHOTS) -> float:
    run(10_000, SEED)  # warm up: builds the decoder
    t = time.perf_counter()
    run(shots, SEED + 1)
    return (time.perf_counter() - t) / shots


def shots_per_seed(p: float, target: int, cap: int) -> int:
    """R3-C6 at failure rate ``p`` (three seeds)."""

    if p <= 0:
        return cap
    n = MIN_SHOTS
    while 3 * n * p < target and n < cap:
        n *= 2
    return min(n, cap)


def _p_l(evaluator: PlacementEvaluator, shots: int = 1_000_000) -> float:
    counts = evaluator.run(shots, [SEED + 100 + k for k in range(-(-shots // 100_000))])
    return counts.logical_failures / counts.shots


def measure() -> dict[str, Any]:
    out: dict[str, Any] = {}
    fez = load_device("fez")
    noise = noise_for("readout", fez)
    rng = np.random.default_rng(SEED)

    # scoring: idle-aware summed error against summed error, per placement
    for device, distance in (("fez", 7), ("miami", 7)):
        stored = load_device(device)
        model = noise_for("readout", stored)
        circuit = repetition_code(distance, 3, "Z", "CZ")
        placements = enumerate_placements(circuit, stored.device)
        sample = [placements[int(i)] for i in rng.choice(len(placements), 200, replace=False)]
        lowering = Lowering(circuit, stored.device, model)
        noisy = [lowering.stim_circuit(m) for m in sample]
        t = time.perf_counter()
        for m in sample:
            summed_error(circuit, model, m)
        summed = (time.perf_counter() - t) / len(sample)
        t = time.perf_counter()
        for c in noisy:
            idle_aware_summed_error(c)
        idle = (time.perf_counter() - t) / len(sample)
        out[f"{device}-{distance}-idle-aware-over-summed"] = idle / summed

    # repetition codes on FakeFez: per-shot cost, d rounds, correlated re-decode, P_L
    for distance in (3, 5, 7):
        circuit3 = repetition_code(distance, 3, "Z", "CZ")
        mapping = enumerate_placements(circuit3, fez.device)[0]
        base = PlacementEvaluator(circuit3, fez.device, noise, mapping)
        three = _per_shot(base.run_chunk)
        redecode = CorrelatedRedecode(base)
        out[f"fez-{distance}-redecode-over-main"] = _per_shot(redecode.run_chunk) / three
        out[f"fez-{distance}-p_l-3-rounds"] = _p_l(base)
        if distance > 3:
            long = PlacementEvaluator(
                repetition_code(distance, distance, "Z", "CZ"), fez.device, noise, mapping
            )
            out[f"fez-{distance}-d-rounds-over-3-rounds"] = _per_shot(long.run_chunk) / three
            out[f"fez-{distance}-p_l-d-rounds"] = _p_l(long)
        out[f"fez-{distance}-seconds-per-shot-here"] = three

    # the surface code on FakeMiami with its couplers restored (R3-S3), d=5, 5 rounds
    lattice, model = restored("miami", "readout")
    surface = surface_code(5, 5, DEFAULT_SCHEDULE, "CZ")
    candidates = enumerate_placements(surface, lattice)
    mapping = candidates[0]
    evaluator = PlacementEvaluator(surface, lattice, model, mapping)
    s5 = _per_shot(evaluator.run_chunk, 50_000)
    out["surface-5-over-fez-7"] = s5 / out["fez-7-seconds-per-shot-here"]
    out["surface-5-redecode-over-main"] = (
        _per_shot(CorrelatedRedecode(evaluator).run_chunk, 50_000) / s5
    )
    out["surface-5-p_l"] = _p_l(evaluator, 500_000)
    lowering = Lowering(surface, lattice, model)
    graph = matching_graph(approximate_dem(lowering.stim_circuit(mapping), decompose=True))
    for k in (10, 80, 640):
        t = time.perf_counter()
        scaled_score_from_graph(graph, ScaledParameters(k, 0.05))
        out[f"surface-5-scaled-k{k}-seconds-here"] = time.perf_counter() - t
    # a candidate is a placement under one hook-safe schedule, as in the pilot's selection
    out["surface-5-candidates-miami-restored"] = sum(
        len(enumerate_placements(surface_code(5, 5, schedule, "CZ"), lattice))
        for schedule in hook_safe_schedules(5, 5, "CZ")
    )
    return out


def project(ratios: dict[str, Any], pilot: dict[str, Any]) -> dict[str, Any]:
    """Revision 5's work on patty, in core-hours and wall hours on 48 workers."""

    fez7 = pilot["measured"]["fez-7-Z"]
    per_shot = float(fez7["main_seconds_per_shot"]["median"])  # patty, FakeFez d=7
    rows = {c["case"]: c for c in pilot["projection"]["cases"]}
    counts = pilot["held_out_placement_counts"]
    lines: list[dict[str, Any]] = []

    def add(item: str, core_hours: float, note: str) -> None:
        lines.append({"item": item, "core_hours": core_hours, "note": note})

    # revision 3's projection, per device (selection and simulation, both settings)
    base = 0.0
    for device in ("kingston", "aachen", "berlin", "nighthawk"):
        for label in ("A", "B", "D5", "D7"):
            r = rows[f"{device}-{label}"]
            base += SETTINGS * (r["selection_core_hours"] + r["simulation_core_hours"])
    add("revision 3's projection (4 devices)", base, "timing_pilot_2.json, unchanged")
    heavy = sum(
        SETTINGS * (rows[f"kingston-{label}"]["selection_core_hours"])
        + SETTINGS * rows[f"kingston-{label}"]["simulation_core_hours"]
        for label in ("A", "B", "D5", "D7")
    )
    add("two more heavy-hex devices", 2 * heavy, "FakeBoston and FakePittsburgh at Kingston's cost")
    # idle-aware summed error on every placement
    summed_s = {
        d: float(pilot["measured"][f"{d}-7-Z"]["seconds_summed"]["median"])
        for d in ("fez", "miami")
    }
    idle = 0.0
    for device in (*HEAVY_HEX, *SQUARE):
        proxy = "fez" if device in HEAVY_HEX else "miami"
        for label in ("A", "B", "D5", "D7"):
            # FakeBoston and FakePittsburgh share FakeKingston's coupling map (section 1)
            n = counts[f"{COUPLING_MAP.get(device, device)}-{label}"]["placements"]
            idle += SETTINGS * n * summed_s[proxy] * ratios[f"{proxy}-7-idle-aware-over-summed"]
    add("idle-aware summed error, every placement", idle / 3600, "ratio to summed error")
    # family placements: at most 5 per case, up from 400 to 4,000 failures
    family = 0.0
    for distance in (3, 5, 7):
        p = ratios[f"fez-{distance}-p_l-3-rounds"]
        cost = per_shot * _scale(ratios, distance)
        extra = 3 * (shots_per_seed(p, *FAMILY) - shots_per_seed(p, *OTHER))
        cases = {3: 2, 5: 1, 7: 1}[distance]  # A and B are d=3
        family += SETTINGS * 4 * cases * 5 * extra * cost  # 4 family devices incl. Nighthawk
    p5 = ratios["surface-5-p_l"]
    s5_cost = per_shot * ratios["surface-5-over-fez-7"]
    family_s5 = SETTINGS * 2 * 5 * 3 * (shots_per_seed(p5, *FAMILY) - shots_per_seed(p5, *OTHER))
    add("family placements to 4,000 failures", family / 3600, "5 placements per case")
    # the surface code: every candidate simulated (none was in either pilot)
    candidates = int(ratios["surface-5-candidates-miami-restored"])
    s5_all = SETTINGS * 2 * candidates * (PILOT_SHOTS + 3 * shots_per_seed(p5, *OTHER))
    add(
        "surface code: every candidate, pilot and main stage",
        (s5_all + family_s5) * s5_cost / 3600,
        f"{candidates} candidates on each square-lattice device (FakeMiami's count, an estimate)",
    )
    scaled = (
        2
        * SETTINGS
        * candidates
        * sum(ratios[f"surface-5-scaled-k{k}-seconds-here"] for k in (10, 80, 640))
    )
    add(
        "surface code: scaled score at k = 10, 80 and 640", scaled / 3600, "cloud seconds, unscaled"
    )
    # sensitivity runs on the confirmatory devices at the headline setting
    redecode = 0.0
    longer = 0.0
    for distance in (3, 5, 7):
        p = ratios[f"fez-{distance}-p_l-3-rounds"]
        cost = per_shot * _scale(ratios, distance)
        n = 3 * shots_per_seed(p, *FAMILY)
        cases = {3: 2, 5: 1, 7: 1}[distance]
        redecode += 3 * cases * 5 * n * cost * ratios[f"fez-{distance}-redecode-over-main"]
        if distance > 3:
            pd = ratios[f"fez-{distance}-p_l-d-rounds"]
            nd = PILOT_SHOTS + 3 * shots_per_seed(pd, *FAMILY)
            longer += 3 * 5 * nd * cost * ratios[f"fez-{distance}-d-rounds-over-3-rounds"]
    redecode += (
        5 * 3 * shots_per_seed(p5, *FAMILY) * s5_cost * ratios["surface-5-redecode-over-main"]
    )
    add("correlated re-decode of the family placements", redecode / 3600, "same samples")
    add("d-round memory for D5 and D7 family placements", longer / 3600, "own pilot")
    total = math.fsum(line["core_hours"] for line in lines)
    berlin_d7 = SETTINGS * rows["berlin-D7"]["selection_core_hours"]
    return {
        "per_shot_patty_fez_d7": per_shot,
        "lines": lines,
        "total_core_hours": total,
        "total_wall_hours_on_48_workers": total / WORKERS,
        "largest_single_case": {
            "case": "berlin-D7 and nighthawk-D7 selection, per device, both settings",
            "wall_hours": berlin_d7 / WORKERS,
        },
        "notes": [
            "development timings only: patty's second pilot, times ratios measured here on "
            "FakeFez and FakeMiami (X16)",
            "placement counts are coupling-map upper bounds; P_L from FakeFez (repetition) "
            "and FakeMiami restored (surface), so a held-out device with lower P_L needs more "
            "shots, up to each tier's cap",
            "the scaled score's seconds are this machine's, not scaled to patty",
        ],
    }


def _scale(ratios: dict[str, Any], distance: int) -> float:
    """Per-shot cost at ``distance`` relative to d=7 (3 rounds), from this machine."""

    return float(
        ratios[f"fez-{distance}-seconds-per-shot-here"] / ratios["fez-7-seconds-per-shot-here"]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", type=Path, default=ROOT / "results_v3" / "timing_pilot" / "projection_v5.json"
    )
    parser.add_argument("--ratios", type=Path, help="reuse an earlier output's ratios")
    args = parser.parse_args(argv)
    pilot = json.loads(PILOT.read_text(encoding="utf-8"))
    if args.ratios is not None:
        ratios = json.loads(args.ratios.read_text(encoding="utf-8"))["ratios"]
    else:
        ratios = measure()
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"schema": SCHEMA, "ratios": ratios}, indent=1) + "\n")
    result = {
        "schema": SCHEMA,
        "description": __doc__,
        "ratios": ratios,
        "projection": project(ratios, pilot),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result["projection"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
