"""Write REPORT_V2.md and results_v2/figures/*.png from results_v2/runs/*.json.

Usage::

    python -m nac_bench.report_v2 [--config configs_v2/benchmark_v2.json]
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from nac_bench.analysis_v2 import (
    METHOD_NAMES,
    METHODS,
    RG7,
    SCORES,
    CaseResult,
    Comparison,
    load,
    r_g7,
)
from noise_aware_compilation.analysis import verdicts_consistent
from noise_aware_compilation.scoring import DEFAULT_SCORER
from noise_aware_compilation.stats import Interval

ROOT = Path(__file__).resolve().parent.parent
DEVICE_TITLES = {"jakarta": "FakeJakartaV2", "hanoi": "FakeHanoiV2", "algiers": "FakeAlgiers"}
CIRCUIT_TITLES = {
    "A": "A (d=3 bit-flip, 3 rounds)",
    "B": "B (d=3 phase-flip, 3 rounds)",
    "D5": "D (d=5 bit-flip, 3 rounds)",
    "D7": "D (d=7 bit-flip, 3 rounds)",
}
DEVICE_ORDER = ["jakarta", "hanoi", "algiers"]
CIRCUIT_ORDER = ["A", "B", "D5", "D7"]
# Dataviz reference palette, categorical slots 1-4 in fixed order (validated: all checks pass;
# the contrast warning for slots 3-4 is relieved by the legend, markers and the tables).
COLORS = {
    "v1": "#2a78d6",
    "decoder-graph": "#eb6834",
    "lowest-summed-error": "#1baf7a",
    "qiskit-vf2": "#eda100",
}
MARKERS = {"v1": "o", "decoder-graph": "s", "lowest-summed-error": "^", "qiskit-vf2": "D"}
COLOR_OTHER = "#8c8b86"
COLOR_GRID = "#e4e3df"
CONSTRAINTS = [
    (
        "K1",
        "Nove's `to_detector_model()` raises for calibrated two-qubit noise with more than one non-zero Pauli term, so the decoder (and both scores) use Stim's approximate detector error model.",
    ),
    ("K2", "CX durations are set per gate name, not per pair."),
    ("K3", "Readout error is symmetric (asymmetric readout averaged)."),
    ("K4", "Gate noise is directional; CX(a,b) and CX(b,a) are separate channels."),
    ("K5", "Nove applies idle noise to every qubit index up to the highest one used."),
    (
        "K6",
        "Noise is a Pauli-channel approximation; coherent errors, crosstalk, leakage and real drift are not modelled.",
    ),
    (
        "K7",
        "The snapshots are realistic noise profiles, not live-device measurements (FakeJakartaV2 is dated after ibmq_jakarta's retirement).",
    ),
    ("K8", "At most 3 neighbours per qubit, so no surface code fits SWAP-free."),
    (
        "K9",
        "Random jitter is synthetic: real drift is correlated across qubits and over time, so Part A measures sensitivity to calibration error, not any device's drift. Every choice is decoded with true-model weights, whereas a real decoder would also use the stale calibration.",
    ),
    (
        "K10",
        "The Qiskit baseline's behaviour depends on the Qiskit version; its results apply to the pinned version only.",
    ),
    (
        "K11",
        "FakeAlgiers has FakeHanoiV2's raw coupling map, so it is held out mainly for noise values, not layout; after conversion the layouts differ only in the removed dead couplers ((15,18) on FakeAlgiers; (5,8) and (19,20) on FakeHanoiV2).",
    ),
]


def fmt(x: float, digits: int = 3) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    if x == 0:
        return "0"
    if 1e-2 <= abs(x) < 1e3:
        return f"{x:.{digits}g}" if abs(x) >= 1 else f"{x:.{digits + 1}f}"
    return f"{x:.{digits - 1}e}"


def fi(i: Interval, digits: int = 3) -> str:
    return f"{fmt(i.estimate, digits)} [{fmt(i.low, digits)}, {fmt(i.high, digits)}]"


def pct(i: Interval) -> str:
    if math.isnan(i.estimate):
        return "n/a"
    if math.isnan(i.low):
        return f"{100 * i.estimate:+.1f}%"
    return f"{100 * i.estimate:+.1f}% [{100 * i.low:+.1f}%, {100 * i.high:+.1f}%]"


def mapping_text(m: Sequence[int]) -> str:
    return "(" + ",".join(str(q) for q in m) + ")"


def title(r: CaseResult) -> str:
    return f"{DEVICE_TITLES[r.device]} {CIRCUIT_TITLES[r.label]}"


def short(r: CaseResult) -> str:
    return f"{DEVICE_TITLES[r.device]} {r.label}"


def ordered(results: dict[str, CaseResult]) -> list[CaseResult]:
    return sorted(
        results.values(), key=lambda r: (DEVICE_ORDER.index(r.device), CIRCUIT_ORDER.index(r.label))
    )


def comparison_cell(c: Comparison) -> str:
    return f"{c.breakdown}; *{c.evidence}*; Δ {fi(c.absolute)}; rel {pct(c.relative)}"


def git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def plot_drift(r: CaseResult, out: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(9, 3.4), constrained_layout=True)
    for ax, metric, label in (
        (axes[0], "m6", "Retained gain M6"),
        (axes[1], "m7", "Harm rate M7"),
    ):
        for k, method in enumerate(METHODS):
            rows = sorted((d for d in r.drift if d.method == method), key=lambda d: d.delta)
            xs, ys, lo, hi = [], [], [], []
            for d in rows:
                value = getattr(d, metric)
                if math.isnan(value.estimate):
                    continue
                xs.append(d.delta + (k - 1.5) * 0.008)
                ys.append(value.estimate)
                lo.append(value.estimate - value.low)
                hi.append(value.high - value.estimate)
            if not xs:
                continue
            ax.errorbar(
                xs,
                ys,
                yerr=[lo, hi],
                color=COLORS[method],
                marker=MARKERS[method],
                ms=5,
                lw=2,
                elinewidth=1,
                capsize=0,
                label=METHOD_NAMES[method],
            )
        ax.set_xlabel("drift strength δ")
        ax.set_title(label, fontsize=10, loc="left")
        ax.grid(axis="y", color=COLOR_GRID, linewidth=0.8)
        ax.spines[["top", "right"]].set_visible(False)
        ax.set_xticks([0, 0.1, 0.25, 0.5])
    axes[0].axhline(1.0, color=COLOR_OTHER, lw=0.8, ls=":")
    axes[1].set_ylim(-0.02, 1.02)
    handles, labels = axes[1].get_legend_handles_labels()
    if not handles:
        handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="outside lower center", ncol=4, frameon=False, fontsize=8)
    fig.suptitle(f"{title(r)}: drift (under this noise model)", fontsize=10, x=0.01, ha="left")
    fig.savefig(out, dpi=130)
    plt.close(fig)


def plot_placements(r: CaseResult, out: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    order = sorted(range(len(r.placements)), key=lambda i: r.placements[i].estimate)
    fig, ax = plt.subplots(figsize=(9, 3.4), constrained_layout=True)
    for x, i in enumerate(order):
        p = r.placements[i].p_l
        ax.errorbar(
            x,
            p.estimate,
            yerr=[[p.estimate - p.low], [p.high - p.estimate]],
            fmt="o",
            ms=3,
            color=COLOR_OTHER,
            elinewidth=1,
            capsize=0,
        )
    position = {i: x for x, i in enumerate(order)}
    fx = position[r.fixed_index]
    ax.plot(
        fx, r.placements[r.fixed_index].estimate, "x", ms=9, mew=2, color="#3d3d3a", label="fixed"
    )
    for method in METHODS:
        choice = r.choices[method]
        if choice is None:
            continue
        ax.plot(
            position[choice],
            r.placements[choice].estimate,
            MARKERS[method],
            ms=9,
            mfc="none",
            mew=2,
            color=COLORS[method],
            label=METHOD_NAMES[method],
        )
    ax.set_yscale("log")
    ax.set_xlabel("valid placements, sorted by P_L")
    ax.set_ylabel("P_L (95% interval)")
    ax.grid(axis="y", color=COLOR_GRID, linewidth=0.8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, fontsize=8, ncol=5, loc="upper left")
    ax.set_title(f"{title(r)}: every valid placement (true model)", fontsize=10, loc="left")
    fig.savefig(out, dpi=130)
    plt.close(fig)


def drift_row(d: Any) -> str:
    if d.gated and not math.isnan(d.m6.estimate):
        m6 = fi(d.m6)
    elif not d.gated:
        m6 = "no gain to retain"
    else:
        m6 = "n/a"
    fail = f"{d.failures}" if d.failures else "0"
    return (
        f"| {METHOD_NAMES[d.method]} | {d.delta:g} | {m6} | {d.harm_draws}/{d.draws - d.failures} "
        f"= {fi(d.m7)} | {d.distinct_choices} | {fail} |"
    )


def overlapping(held: Sequence[CaseResult]) -> str:
    names = []
    for r in held:
        g, v = r.choices["decoder-graph"], r.choices["v1"]
        if g is None or v is None or g == v:
            continue
        a, b = r.placements[g].p_l, r.placements[v].p_l
        if a.low <= b.high and b.low <= a.high:
            names.append(short(r))
    return ", ".join(names) if names else "none"


def harm_text(d: Any) -> str:
    return f"{d.harm_draws}/{d.draws - d.failures}"


def answer_part_a(dev: Sequence[CaseResult]) -> list[str]:
    lines = []
    for method in METHODS:
        drifted = [r for r in dev if r.drift]
        if not drifted:
            continue
        strongest = max(d.delta for d in drifted[0].drift)

        def at(r: CaseResult, delta: float, method: str = method) -> Any:
            return next(d for d in r.drift if d.method == method and d.delta == delta)

        gated = [r for r in drifted if at(r, 0.0).gated]
        if gated:
            parts = "; ".join(f"{short(r)} {fi(at(r, strongest).m6)}" for r in gated)
            gain_text = (
                f"where its δ = 0 choice beat fixed ({len(gated)} of {len(drifted)} cases), the "
                f"mean retained gain M6 at δ = {strongest:g} was {parts}"
            )
        else:
            gain_text = (
                f"its δ = 0 choice did not beat fixed in any of the {len(drifted)} cases, so "
                "there is no gain to retain"
            )
        harm = ", ".join(
            f"{short(r)} {harm_text(at(r, strongest))} ({harm_text(at(r, 0.0))})" for r in drifted
        )
        lines.append(
            f"- **{METHOD_NAMES[method]}:** {gain_text}. Draws whose choice is worse than fixed "
            f"(M7) at δ = {strongest:g}, with δ = 0 in brackets: {harm}."
        )
    lines.append("")
    lines.append(
        "M6 above 1 means the seen-model choices were on average better than the method's own "
        "true-model choice, which happens when that choice is far from the best placement."
    )
    return lines


def answer_part_b(cases: Sequence[CaseResult], score: str) -> str:
    out = []
    for reference in ("fixed", "average", "lowest-summed-error", "qiskit-vf2"):
        tally: dict[str, int] = {"better": 0, "indistinguishable": 0, "worse": 0}
        for r in cases:
            c = r.comparisons.get(score, {}).get(reference)
            if c is not None:
                tally[c.verdict] += 1
        out.append(
            f"vs {METHOD_NAMES[reference]}: better {tally['better']}, indistinguishable "
            f"{tally['indistinguishable']}, worse {tally['worse']}"
        )
    return "; ".join(out)


def build_report(results: dict[str, CaseResult], config: dict[str, Any], figures: Path) -> str:
    cases = ordered(results)
    dev = [r for r in cases if r.role == "development"]
    held = [r for r in cases if r.role == "held-out"]
    rg7: RG7 | None = r_g7(held) if len(held) == 4 else None
    lines: list[str] = []
    w = lines.append
    w("# Noise-Aware Placement for Nove: v2 Report")
    w("")
    w(
        "Generated by `python -m nac_bench.report_v2` from `results_v2/runs/*.json` "
        f"(code commit `{git_head()[:7]}`). Spec: [SPEC_V2.md](SPEC_V2.md) (rev 1); "
        "open choices: [DECISIONS_V2.md](DECISIONS_V2.md). v1's report "
        "([REPORT.md](REPORT.md)) and results are unchanged (X14)."
    )
    w("")
    w(
        "**Every result here holds under this noise model only** (the converted calibration "
        "snapshots with Nove's Pauli-channel noise, decoded by PyMatching); none is a claim about "
        "hardware. **FakeJakartaV2 and FakeHanoiV2 results are development-device results** "
        "(R2-R7): the decoder-graph score was developed on those devices, so fresh seeds do not "
        "make them an independent test. **Only FakeAlgiers is the real test.**"
    )
    w("")
    w(
        "Verdicts follow v1's rules: *better*/*worse* only when 95% intervals do not overlap, computed "
        "from all three seeds' shots pooled, with the per-seed tally next to it. A gain is claimed only "
        "for a pooled *better*, labelled *weak evidence* when not every seed shows it (R2-R3)."
    )
    w("")

    # 1. answers
    w("## 1. Answers to the three questions (R2-R5)")
    w("")
    w(
        "**1. Does noise-aware placement still help when it chooses with stale calibration data?** "
        "(Part A, section 3; development devices.)"
    )
    w("")
    lines.extend(answer_part_a(dev))
    w("")
    w(
        "**2. Does it beat placement methods people already use?** (Part B, section 2; pooled verdicts "
        "of each score's choice, development devices and FakeAlgiers.)"
    )
    w("")
    for score in SCORES:
        w(
            f"- **{METHOD_NAMES[score]}**, development devices ({len(dev)} cases): {answer_part_b(dev, score)}."
        )
        w(
            f"- **{METHOD_NAMES[score]}**, FakeAlgiers ({len(held)} cases): {answer_part_b(held, score)}."
        )
    w("")
    w(
        "**3. Does the decoder-graph score choose better than the v1 score on a device it was never "
        "developed on?** (Part C, section 4.)"
    )
    w("")
    if rg7 is not None:
        verdict_text = (
            "yes: its mean relative regret is lower and the 95% interval of the difference excludes zero"
            if rg7.decoder_graph_wins
            else "not shown: the 95% interval of the difference in mean relative regret does not lie below zero"
        )
        w(
            f"- On FakeAlgiers, {verdict_text} (difference decoder-graph minus v1 = "
            f"{fi(rg7.difference)}). By R-G7 the default score is **{rg7.default_scorer}**. "
            "This verdict covers only these four cases (A, B, D5, D7 on FakeAlgiers)."
        )
    else:
        w(f"- Not answered: R-G7 needs the four FakeAlgiers cases and {len(held)} are present.")
    w("")

    # 2. Part B
    w("## 2. Part B: each score's choice vs every baseline (R-B9, R2-R2, R2-R3, T2-P3)")
    w("")
    w(
        "Δ = chosen − reference P_L (negative: the score's choice has the lower P_L), with its 95% "
        "interval; rel is Δ relative to the reference. *Same placement* means both picked the same "
        "mapping."
    )
    w("")
    for r in cases:
        tag = "development device" if r.role == "development" else "held-out device"
        w(f"### {title(r)} ({tag})")
        w("")
        w("| Score | Reference | Verdict | Claim; difference |")
        w("| --- | --- | --- | --- |")
        for score in SCORES:
            for reference in ("fixed", "average", "lowest-summed-error", "qiskit-vf2"):
                c = r.comparisons.get(score, {}).get(reference)
                if c is None:
                    w(
                        f"| {METHOD_NAMES[score]} | {METHOD_NAMES[reference]} | baseline failed (R-B7) | |"
                    )
                    continue
                same = (
                    r.choices[score]
                    == (r.fixed_index if reference == "fixed" else r.choices.get(reference))
                    and reference != "average"
                )
                note = " (same placement)" if same else ""
                default = " (default)" if score == DEFAULT_SCORER else ""
                w(
                    f"| {METHOD_NAMES[score]}{default} | {METHOD_NAMES[reference]}{note} | "
                    f"{c.breakdown} | *{c.evidence}*; Δ {fi(c.absolute)}; rel {pct(c.relative)} |"
                )
        w("")
    w("Claims (pooled *better* or *worse*):")
    w("")
    any_claim = False
    for r in cases:
        for score in SCORES:
            for reference, c in r.comparisons.get(score, {}).items():
                if c.verdict in ("better", "worse"):
                    any_claim = True
                    label = "gain" if c.verdict == "better" else "loss"
                    weak = " (weak evidence)" if "weak evidence" in c.evidence else ""
                    w(
                        f"- {title(r)}, {METHOD_NAMES[score]} vs {METHOD_NAMES[reference]}: **{label}**"
                        f"{weak}, rel {pct(c.relative)} ({c.breakdown})"
                    )
    if not any_claim:
        w("- none")
    w("")
    consistent = all(
        verdicts_consistent(c.seed_verdicts)
        for r in cases
        for refs in r.comparisons.values()
        for c in refs.values()
    )
    w(
        f"No two verdicts for one comparison are opposite across seeds: **{'yes' if consistent else 'NO'}**."
    )
    w("")

    # 3. Part A
    w("## 3. Part A: robustness to stale calibration (R2-R1, T2-P1, T2-P2; development devices)")
    w("")
    w(
        f"Drift strengths {config['drift']['strengths']}, {config['drift']['draws']} seen models per "
        "strength, each method given the identical seen model per draw; every choice graded with the "
        "true-model measurements. M6 = (P_L fixed − P_L of the seen-model choice) ÷ (P_L fixed − P_L "
        "of the true-model choice), averaged over draws, 95% bootstrap interval over draws and shot "
        f"noise ({config['bootstrap']['replicates']} replicates); reported only where that method's "
        "δ = 0 choice is pooled *better* than fixed. M7 = draws whose choice is pooled *worse* than "
        "fixed, Wilson 95% interval over draws."
    )
    w("")
    for r in [r for r in cases if r.drift]:
        w(f"### {title(r)}")
        w("")
        fig = figures / f"{r.case_id}-drift.png"
        plot_drift(r, fig)
        w(f"![{title(r)} drift]({fig.relative_to(ROOT).as_posix()})")
        w("")
        w("| Method | δ | M6 retained gain | M7 harm (draws) | distinct choices | failures |")
        w("| --- | --- | --- | --- | --- | --- |")
        for method in METHODS:
            for d in sorted((d for d in r.drift if d.method == method), key=lambda d: d.delta):
                w(drift_row(d))
        w("")

    # 4. Part C
    w("## 4. Part C: held-out evaluation on FakeAlgiers (R2-R4, T2-P4)")
    w("")
    w(
        "The decoder-graph score was frozen (commit recorded in `configs_v2/freeze.json`) before "
        "FakeAlgiers was converted or any FakeAlgiers result existed. Relative regret = (P_L of the "
        "choice − oracle-best P_L) ÷ oracle-best P_L."
    )
    w("")
    w("| Case | Score | Choice | P_L | M4 | Rank (1 = best) | Regret (M5) | Relative regret |")
    w("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in held:
        for score in SCORES:
            i = r.choices[score]
            assert i is not None
            w(
                f"| {CIRCUIT_TITLES[r.label]} | {METHOD_NAMES[score]} | {mapping_text(r.placements[i].mapping)} "
                f"| {fi(r.placements[i].p_l)} | {fmt(r.m4[score])} | {fmt(r.rank[score])} of "
                f"{len(r.placements)} | {fi(r.regret[score])} | {fmt(r.relative_regret[score])} |"
            )
    w("")
    if rg7 is not None:
        w(
            "**R-G7 calculation.** Per case, difference in relative regret (decoder-graph − v1) = "
            "(P_L graph choice − P_L v1 choice) ÷ oracle-best P_L:"
        )
        w("")
        w("| Case | Relative regret, v1 | Relative regret, decoder-graph | Difference |")
        w("| --- | --- | --- | --- |")
        for case_id, rr1, rrg, diff in rg7.per_case:
            w(f"| {case_id} | {fmt(rr1)} | {fmt(rrg)} | {fmt(diff)} |")
        w("")
        w(
            f"Mean difference over the four cases = {fi(rg7.difference)} (95% delta-method interval "
            "from shot noise only; the cases are fixed). The decoder-graph score becomes the default "
            "only if this interval lies wholly below zero. "
            f"**Outcome: {'it does' if rg7.decoder_graph_wins else 'it does not'}; the default score "
            f"is `{rg7.default_scorer}`** (the package's current default: `{DEFAULT_SCORER}`). "
            "This verdict covers only these four cases."
        )
        w("")
        w(
            "The oracle-best placement is the one with the lowest measured P_L, so a score that "
            "picks it gets a relative regret of exactly 0, while a score one rank away is charged "
            "for any shot-noise gap. The interval accounts for that gap's noise, but not for which "
            "placement came out best. Cases where the two scores chose different placements whose "
            f"own P_L intervals overlap: {overlapping(held)}; there the per-case difference is "
            "within the noise of the ranking."
        )
        w("")

    # 5. Per-case details
    w("## 5. Every case: headroom, choices and score validity (R-R1, R-R3, R-R4, R-R5)")
    w("")
    w(
        "| Case | Valid placements | Shots per seed | Best P_L | Worst P_L | Headroom | Fixed P_L | Average P_L | M4 v1 / graph / summed |"
    )
    w("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in cases:
        b, wo = r.placements[r.best_index], r.placements[r.worst_index]
        headroom = (
            "placement does not matter (intervals overlap)"
            if r.headroom_overlap
            else "placement matters"
        )
        w(
            f"| {title(r)} | {len(r.placements)} | {r.raw['shots']['shots_per_seed']:,} | {fi(b.p_l)} | "
            f"{fi(wo.p_l)} | {headroom} | {fi(r.placements[r.fixed_index].p_l)} | {fi(r.average)} | "
            f"{fmt(r.m4['v1'])} / {fmt(r.m4['decoder-graph'])} / {fmt(r.m4['lowest-summed-error'])} |"
        )
    w("")
    w(
        "A negative M4 would mean the score ranks placements backwards; it is flagged as **negative** below."
    )
    w("")
    w("| Case | Method | Choice | P_L | Rank | Regret (M5) |")
    w("| --- | --- | --- | --- | --- | --- |")
    for r in cases:
        for method in METHODS:
            i = r.choices[method]
            if i is None:
                w(f"| {title(r)} | {METHOD_NAMES[method]} | failed ({r.qiskit_failure}) | | | |")
                continue
            flag = " **negative M4**" if method in r.m4 and r.m4[method] < 0 else ""
            w(
                f"| {title(r)} | {METHOD_NAMES[method]}{flag} | {mapping_text(r.placements[i].mapping)} | "
                f"{fi(r.placements[i].p_l)} | {fmt(r.rank[method])} | {fi(r.regret[method])} |"
            )
    w("")
    for r in cases:
        fig = figures / f"{r.case_id}-placements.png"
        plot_placements(r, fig)
        w(f"![{title(r)} placements]({fig.relative_to(ROOT).as_posix()})")
        w("")

    # 6. decoder
    w("## 6. Decoder (R-R6)")
    w("")
    w(
        "PyMatching, built separately for each placement from that placement's own Stim detector "
        "error model (`approximate_disjoint_errors=True`, `decompose_errors=True`) under the true "
        "model. **The decoder model is approximate** (K1), and every results file records this."
    )
    w("")
    w("## 7. Sensitivity (R-R7)")
    w("")
    w(
        "The T-L1 sensitivity reruns are v1's and are reported in REPORT.md section 10; the v2 spec "
        "adds no sensitivity variants, so none were run for v2."
    )
    w("")
    w("## 8. Limitations (R-R8)")
    w("")
    for key, text in CONSTRAINTS:
        w(f"- **{key}.** {text}")
    w(
        "- **Development devices.** FakeJakartaV2 and FakeHanoiV2 results are not an independent "
        "test of the decoder-graph score (R2-R7)."
    )
    w("- **R-G7 scope.** The held-out verdict rests on four cases on one device.")
    w("")
    w("## 9. Changes to the spec (R2-R6)")
    w("")
    w(
        "SPEC_V2.md revision 1 (Oct 11, 2026) was made **before any v2 result existed**, after the "
        "pre-implementation review; its note lists every change and reason. No change was made after "
        "results were seen."
    )
    w("")
    w("## 10. Appendix: every placement (R-R1)")
    w("")
    for r in cases:
        w(f"### {title(r)}")
        w("")
        w("| Mapping | P_L (95%) | Detection-event rate M3 | Chosen by |")
        w("| --- | --- | --- | --- |")
        for i, p in sorted(enumerate(r.placements), key=lambda t: t[1].estimate):
            by = [METHOD_NAMES[m] for m in METHODS if r.choices[m] == i]
            if i == r.fixed_index:
                by.append("fixed")
            w(f"| {mapping_text(p.mapping)} | {fi(p.p_l)} | {fi(p.detection)} | {', '.join(by)} |")
        w("")
    w("## 11. Provenance and reproduction")
    w("")
    any_raw = cases[0].raw["provenance"] if cases else {}
    w(
        f"Nove `{any_raw.get('nove_commit')}`, Stim {any_raw.get('stim_version')}, PyMatching "
        f"{any_raw.get('pymatching_version')}, Qiskit {any_raw.get('qiskit_version')}, "
        f"qiskit-ibm-runtime {any_raw.get('qiskit_ibm_runtime_version')} (device files). Base seeds "
        f"{config['base_seeds']} (v1 used 20261011-20261013). Every results file records its code "
        "commit, config hash, true- and seen-model hashes, drift seeds and every chunk seed."
    )
    w("")
    w("```bash")
    w("python -m pip install -e '.[dev,bench]'")
    w("python -m nac_bench.run_v2 configs_v2/benchmark_v2.json && python -m nac_bench.report_v2")
    w("```")
    w("")
    return "\n".join(lines)


def summary_json(results: dict[str, CaseResult]) -> dict[str, Any]:
    cases = ordered(results)
    held = [r for r in cases if r.role == "held-out"]
    out: dict[str, Any] = {"cases": {}}
    for r in cases:
        out["cases"][r.case_id] = {
            "choices": {
                m: None if i is None else list(r.placements[i].mapping)
                for m, i in r.choices.items()
            },
            "verdicts": {
                s: {ref: c.verdict for ref, c in refs.items()} for s, refs in r.comparisons.items()
            },
            "seed_verdicts": {
                s: {ref: list(c.seed_verdicts) for ref, c in refs.items()}
                for s, refs in r.comparisons.items()
            },
            "m4": r.m4,
            "relative_regret": r.relative_regret,
            "drift": [
                {
                    "method": d.method,
                    "delta": d.delta,
                    "gated": d.gated,
                    "m6": d.m6.to_dict(),
                    "m7": d.m7.to_dict(),
                    "harm_draws": d.harm_draws,
                    "draws": d.draws,
                    "failures": d.failures,
                }
                for d in r.drift
            ],
        }
    if len(held) == 4:
        rg7 = r_g7(held)
        out["r_g7"] = {
            "difference": rg7.difference.to_dict(),
            "per_case": [list(row) for row in rg7.per_case],
            "decoder_graph_wins": rg7.decoder_graph_wins,
            "default_scorer": rg7.default_scorer,
        }
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write REPORT_V2.md from results_v2/runs")
    parser.add_argument("--config", type=Path, default=ROOT / "configs_v2" / "benchmark_v2.json")
    parser.add_argument("--runs", type=Path, default=ROOT / "results_v2" / "runs")
    parser.add_argument("--out", type=Path, default=ROOT / "REPORT_V2.md")
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    results = load(args.runs, config)
    figures = ROOT / "results_v2" / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build_report(results, config, figures) + "\n", encoding="utf-8")
    (ROOT / "results_v2" / "summary.json").write_text(
        json.dumps(summary_json(results), indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
