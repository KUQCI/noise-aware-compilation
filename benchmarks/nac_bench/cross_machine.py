"""T3-R2: compare v2 cases rerun on another machine with the committed run files.

Usage::

    python -m nac_bench.run_v2 configs_v2/benchmark_v2.json --only v2-hanoi-A v2-algiers-D7 \\
        --out ~/nac/t3r2 --workers 48
    python -m nac_bench.cross_machine ~/nac/t3r2 v2-hanoi-A v2-algiers-D7 --machine-label patty

For each case it checks that every method's selection (under the true model and under each
drift draw) is the same, and that every placement's counts on every base seed are bit for
bit the committed ones. Where counts differ, it checks whether each placement's pooled
Wilson 95% interval overlaps its committed interval, SPEC_V3.md's fallback. The summary
(small JSON) is printed and, with ``--out``, written.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from nac_bench.machine import machine
from noise_aware_compilation.stats import wilson

ROOT = Path(__file__).resolve().parent.parent
COMMITTED = ROOT / "results_v2" / "runs"
COUNT_FIELDS = ("shots", "logical_failures", "detector_fires", "detector_fires_sq", "chunk_seeds")


def _mappings(choices: dict[str, Any]) -> dict[str, Any]:
    return {name: c.get("mapping") for name, c in choices.items()}


def compare(committed: dict[str, Any], rerun: dict[str, Any]) -> dict[str, Any]:
    """The T3-R2 record for one case."""

    selections_equal = _mappings(committed["true_model"]["choices"]) == _mappings(
        rerun["true_model"]["choices"]
    )
    drift_equal = [_mappings(r["choices"]) for r in committed["drift"]["records"]] == [
        _mappings(r["choices"]) for r in rerun["drift"]["records"]
    ]
    a, b = committed["placements"], rerun["placements"]
    if [p["mapping"] for p in a] != [p["mapping"] for p in b]:
        return {"same_placements": False}
    differing, overlapping = 0, 0
    for pa, pb in zip(a, b, strict=True):
        same = all(
            {k: sa[k] for k in COUNT_FIELDS} == {k: sb[k] for k in COUNT_FIELDS}
            for sa, sb in zip(pa["per_seed"], pb["per_seed"], strict=True)
        )
        if same:
            continue
        differing += 1
        wa = wilson(
            sum(s["logical_failures"] for s in pa["per_seed"]),
            sum(s["shots"] for s in pa["per_seed"]),
        )
        wb = wilson(
            sum(s["logical_failures"] for s in pb["per_seed"]),
            sum(s["shots"] for s in pb["per_seed"]),
        )
        overlapping += int(wa.low <= wb.high and wb.low <= wa.high)
    return {
        "same_placements": True,
        "placements": len(a),
        "shots_per_seed": [committed["shots"]["shots_per_seed"], rerun["shots"]["shots_per_seed"]],
        "selections_equal": selections_equal,
        "drift_selections_equal": drift_equal,
        "drift_draws": len(committed["drift"]["records"]),
        "placements_with_different_counts": differing,
        "of_those_with_overlapping_intervals": overlapping,
        "bit_identical": selections_equal and drift_equal and differing == 0,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("rerun_dir", type=Path)
    parser.add_argument("cases", nargs="+")
    parser.add_argument("--committed", type=Path, default=COMMITTED)
    parser.add_argument("--machine-label", default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    records = {}
    for case in args.cases:
        committed = json.loads((args.committed / f"{case}.json").read_text(encoding="utf-8"))
        rerun = json.loads((args.rerun_dir / f"{case}.json").read_text(encoding="utf-8"))
        record = compare(committed, rerun)
        record["rerun_package_git_commit"] = rerun["provenance"]["package_git_commit"]
        record["rerun_package_git_dirty"] = rerun["provenance"]["package_git_dirty"]
        record["rerun_wall_clock_seconds"] = rerun["wall_clock_seconds"]
        record["rerun_workers"] = rerun["workers"]
        records[case] = record
    summary = {
        "schema": "noise-aware-compilation.cross-machine-v3/1",
        "test": "SPEC_V3.md T3-R2",
        "cases": records,
        "all_bit_identical": all(r.get("bit_identical") for r in records.values()),
        "machine": machine(args.machine_label),
    }
    text = json.dumps(summary, indent=1, sort_keys=True)
    print(text)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
