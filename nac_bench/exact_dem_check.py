"""How much the approximate detector model costs the decoder (SPEC_V3.md revision 6, K21).

Usage::

    python -m nac_bench.exact_dem_check [--out results_v3/k1/exact_dem.json]

v1's K1 says the decoder model is approximate because Nove's own detector model refuses
two-qubit channels with more than one Pauli term. An exact model exists for this noise: each
CZ channel is 15 equal terms, which is DEPOLARIZE2 exactly, and each single-qubit Pauli
channel factors exactly into independent X, Y and Z flips. This check writes that exact
circuit, builds PyMatching from its detector model and from Stim's approximate one (the one
every run uses), and decodes the same samples with both, on development devices only (X16):
FakeFez D5 and D7 and FakeMiami D5, five seeded placements each, 3 rounds, CZ, reset =
readout. It reports each model's failures, the shots on which the two decoders disagree and
the paired difference with a 95% interval, and the largest relative difference between the
two models' mechanism probabilities.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path
from typing import Any

import numpy as np
import pymatching
import stim

from nac_bench.run_reset import noise_for
from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.devices import load_device
from noise_aware_compilation.fast_lowering import Lowering
from noise_aware_compilation.placement import approximate_dem, enumerate_placements

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "noise-aware-compilation.exact-dem-check/1"
SEED = 20261112
SHOTS = 1_000_000
CASES = (("fez", 5), ("fez", 7), ("miami", 5))
PLACEMENTS = 5


def exact_circuit(noisy: stim.Circuit) -> stim.Circuit:
    """``noisy`` with every channel written as independent mechanisms, exactly."""

    out = stim.Circuit()
    for instruction in noisy.flattened():
        name, args = instruction.name, instruction.gate_args_copy()
        targets = instruction.targets_copy()
        if name == "PAULI_CHANNEL_1":
            px, py, pz = args
            p0 = 1.0 - px - py - pz
            lx, ly, lz = p0 + px - py - pz, p0 - px + py - pz, p0 - px - py + pz
            if min(lx, ly, lz) <= 0:
                raise ValueError("a single-qubit channel does not factor")
            # independent X(a), Y(b), Z(c): lx = (1-2b)(1-2c), ly = (1-2a)(1-2c), ...
            a = (1 - math.sqrt(ly * lz / lx)) / 2
            b = (1 - math.sqrt(lx * lz / ly)) / 2
            c = (1 - math.sqrt(lx * ly / lz)) / 2
            for gate, q in (("X_ERROR", a), ("Y_ERROR", b), ("Z_ERROR", c)):
                if q > 0:
                    out.append(gate, targets, q)
        elif name == "PAULI_CHANNEL_2":
            if max(args) - min(args) > 1e-15 * max(args):
                raise ValueError("a two-qubit channel is not uniform")
            out.append("DEPOLARIZE2", targets, math.fsum(args))
        else:
            out.append(instruction)
    return out


def _table(dem: stim.DetectorErrorModel) -> dict[str, float]:
    table: dict[str, float] = {}
    for instruction in dem.flattened():
        if instruction.type == "error":
            key = str(instruction.targets_copy())
            table[key] = table.get(key, 0.0) + float(instruction.args_copy()[0])
    return table


def check_placement(noisy: stim.Circuit, shots: int, seed: int) -> dict[str, Any]:
    approx_dem = approximate_dem(noisy, decompose=True)
    exact_dem = exact_circuit(noisy).detector_error_model(
        decompose_errors=True, ignore_decomposition_failures=True
    )
    a, e = (
        _table(approximate_dem(noisy, decompose=False)),
        _table(exact_circuit(noisy).detector_error_model()),
    )
    rel = [abs(a.get(k, 0.0) - e[k]) / e[k] for k in e if e[k] > 0]
    dets, obs = noisy.compile_detector_sampler(seed=seed).sample(shots, separate_observables=True)
    wrong = {}
    for name, dem in (("approximate", approx_dem), ("exact", exact_dem)):
        predicted = pymatching.Matching.from_detector_error_model(dem).decode_batch(dets)
        wrong[name] = np.any(predicted != obs, axis=1)
    only_a = int(np.count_nonzero(wrong["approximate"] & ~wrong["exact"]))
    only_e = int(np.count_nonzero(wrong["exact"] & ~wrong["approximate"]))
    diff = (only_a - only_e) / shots
    half = 1.96 * math.sqrt(max(only_a + only_e - (only_a - only_e) ** 2 / shots, 0)) / shots
    return {
        "shots": shots,
        "seed": seed,
        "failures_approximate_model": int(np.count_nonzero(wrong["approximate"])),
        "failures_exact_model": int(np.count_nonzero(wrong["exact"])),
        "only_approximate_fails": only_a,
        "only_exact_fails": only_e,
        "p_l_difference_approximate_minus_exact": diff,
        "p_l_difference_95": [diff - half, diff + half],
        "mechanisms": len(e),
        "max_relative_probability_difference": max(rel),
        "median_relative_probability_difference": float(np.median(rel)),
    }


def run(shots: int = SHOTS) -> dict[str, Any]:
    rng = random.Random(SEED)
    rows = []
    for device, distance in CASES:
        stored = load_device(device)
        noise = noise_for("readout", stored)
        circuit = repetition_code(distance, 3, "Z", "CZ")
        lowering = Lowering(circuit, stored.device, noise)
        placements = enumerate_placements(circuit, stored.device)
        for k, i in enumerate(sorted(rng.sample(range(len(placements)), PLACEMENTS))):
            row = check_placement(lowering.stim_circuit(placements[i]), shots, SEED + 100 * k)
            rows.append({"device": device, "distance": distance, "index": i, **row})
    failures_a = sum(r["failures_approximate_model"] for r in rows)
    failures_e = sum(r["failures_exact_model"] for r in rows)
    return {
        "schema": SCHEMA,
        "description": __doc__,
        "rows": rows,
        "summary": {
            "placements": len(rows),
            "max_relative_probability_difference": max(
                r["max_relative_probability_difference"] for r in rows
            ),
            "median_relative_probability_difference": float(
                np.median([r["median_relative_probability_difference"] for r in rows])
            ),
            "total_failures_approximate_model": failures_a,
            "total_failures_exact_model": failures_e,
            "relative_change_exact_over_approximate": failures_e / failures_a - 1,
            "placements_whose_paired_interval_excludes_zero": sum(
                1 for r in rows if r["p_l_difference_95"][0] > 0 or r["p_l_difference_95"][1] < 0
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "results_v3" / "k1" / "exact_dem.json")
    parser.add_argument("--shots", type=int, default=SHOTS)
    args = parser.parse_args(argv)
    result = run(args.shots)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
