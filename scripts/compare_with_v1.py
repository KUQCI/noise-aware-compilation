"""Write results/v1_comparison.md: rev 2 results next to the v1 results they replace.

v1's results files are read from git (commit 2ca4026, the v1 handback) into a temporary
folder, so nothing outside the repository is needed. Every number is computed by the
package's own analysis from the raw counts.

Usage: python scripts/compare_with_v1.py
"""

from __future__ import annotations

import subprocess
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path

from noise_aware_compilation.analysis import CaseSummary, load_summaries
from noise_aware_compilation.report import mapping_text, summary_cell

ROOT = Path(__file__).resolve().parent.parent
V1_COMMIT = "2ca4026c44e332b1416c114a8b36360c28e2504b"
DEVICE_TITLES = {"jakarta": "FakeJakartaV2", "hanoi": "FakeHanoiV2"}


def _v1_summaries(tmp: Path) -> dict[str, CaseSummary]:
    archive = subprocess.run(
        ["git", "archive", "--format=tar", V1_COMMIT, "results/runs"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout
    with tarfile.open(fileobj=BytesIO(archive)) as tar:
        tar.extractall(tmp, filter="data")
    return load_summaries(tmp / "results" / "runs")


def _row(label: str, s: CaseSummary) -> str:
    chosen = s.placements[s.chosen_index]
    fixed = s.placements[s.fixed_index]
    return (
        f"| {label} | {len(s.placements)} | {mapping_text(chosen.mapping)} "
        f"| {mapping_text(fixed.mapping)} | {summary_cell(s, 'vs_fixed')} "
        f"| {summary_cell(s, 'vs_average')} | {s.spearman:+.3f} "
        f"| {s.chosen_rank:g} of {len(s.placements)} |"
    )


def main() -> int:
    new = load_summaries(ROOT / "results" / "runs")
    with tempfile.TemporaryDirectory() as tmp:
        old = _v1_summaries(Path(tmp))
    lines = [
        "# Rev 2 vs v1 under real converted noise (N2)",
        "",
        "Every result holds **under this noise model** only. v1 capped CX entries with "
        "gate_error = 1 at 1/15 per term; rev 2 removes such couplers in both directions "
        "(SPEC.md rev 2, DECISIONS.md D3). *Rev 2, couplers restored* is the T-L1 variant "
        "that puts them back at their better direction's error. FakeJakartaV2 has no such "
        "coupler, so its rows should be identical. Verdicts are pooled over the three seeds, "
        "with the per-seed tally and evidence label of SPEC.md rev 4 next to them. Generated "
        "by `scripts/compare_with_v1.py`.",
        "",
        "| Run | Placements | Chosen | Fixed | vs fixed | vs average | M4 Spearman | M5 rank |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for case_id in sorted(new, key=lambda c: (c.split("-")[0] != "jakarta", c)):
        s = new[case_id]
        if s.case["condition"] != "N2" or s.case["variant"] != "base":
            continue
        title = f"**{DEVICE_TITLES[s.case['device']]} {s.case['circuit']['label']}**"
        lines.append(f"| {title} | | | | | | | |")
        if case_id in old:
            lines.append(_row("v1", old[case_id]))
        lines.append(_row("rev 2", s))
        restored = new.get(f"{case_id}-L1-coupler-better")
        if restored is not None and s.case["device"] != "jakarta":
            lines.append(_row("rev 2, couplers restored", restored))
    out = ROOT / "results" / "v1_comparison.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
