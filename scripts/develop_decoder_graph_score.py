"""Development check of the decoder-graph score on the development devices only (v2 R-G4).

Reads v1's committed N2 results for FakeJakartaV2 and FakeHanoiV2 (read-only, X14) and
prints, per case, M4 (Spearman between score and measured P_L) and M5 regret for the v1
score and the decoder-graph score. These are development numbers measured on v1 seeds;
they are not v2 evaluation results. FakeAlgiers is never read here (X12).

Usage: python scripts/develop_decoder_graph_score.py > results_v2/development/decoder_graph.txt
"""

from __future__ import annotations

import json
from pathlib import Path

from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.decoder_graph import decoder_graph_score
from noise_aware_compilation.devices import load_device
from noise_aware_compilation.stats import spearman

ROOT = Path(__file__).resolve().parent.parent
CASES = [f"jakarta-{c}" for c in ("A1", "A3", "A5", "B")] + [
    f"hanoi-{c}" for c in ("A1", "A3", "A5", "B", "D5", "D7")
]


def main() -> int:
    print("Development data: v1 N2 results (results/runs, v1 seeds). Not v2 evaluation results.")
    print(
        f"{'case':12s} {'n':>4s} {'M4 v1':>7s} {'M4 graph':>9s} "
        f"{'regret v1':>10s} {'regret graph':>12s} {'best P_L':>9s}"
    )
    for case in CASES:
        raw = json.loads((ROOT / "results" / "runs" / f"{case}-N2.json").read_text())
        spec = raw["case"]["circuit"]
        stored = load_device(raw["case"]["device"])
        circuit = repetition_code(spec["distance"], spec["rounds"], spec["basis"])
        p_l, v1, graph = [], [], []
        for p in raw["placements"]:
            failures = sum(s["logical_failures"] for s in p["per_seed"])
            shots = sum(s["shots"] for s in p["per_seed"])
            p_l.append(failures / shots)
            v1.append(float(p["score"]))
            graph.append(decoder_graph_score(circuit, stored.device, stored.noise, p["mapping"]))
        mappings = [tuple(p["mapping"]) for p in raw["placements"]]
        best = min(p_l)

        def pick(scores: list[float], mappings: list[tuple[int, ...]] = mappings) -> int:
            return min(range(len(scores)), key=lambda i: (float(f"{scores[i]:.11e}"), mappings[i]))

        print(
            f"{case:12s} {len(p_l):4d} {spearman(v1, p_l):+7.3f} {spearman(graph, p_l):+9.3f} "
            f"{p_l[pick(v1)] - best:10.2e} {p_l[pick(graph)] - best:12.2e} {best:9.2e}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
