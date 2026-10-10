"""Write REPORT.md and results/figures/*.png from results/runs/*.json (R-D9).

Usage::

    python -m noise_aware_compilation.report [--runs results/runs] [--out REPORT.md]
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from noise_aware_compilation.analysis import (
    CaseSummary,
    Comparison,
    load_summaries,
    precision_ok,
    trend,
)
from noise_aware_compilation.stats import Interval

DEVICE_TITLES = {"jakarta": "FakeJakartaV2", "hanoi": "FakeHanoiV2"}
CIRCUIT_TITLES = {
    "A1": "A (d=3 bit-flip, 1 round)",
    "A3": "A (d=3 bit-flip, 3 rounds)",
    "A5": "A (d=3 bit-flip, 5 rounds)",
    "B": "B (d=3 phase-flip, 3 rounds)",
    "C": "C (d=2, 3 rounds; M3 only)",
    "D5": "D (d=5 bit-flip, 3 rounds)",
    "D7": "D (d=7 bit-flip, 3 rounds)",
}
CONDITION_TITLES = {
    "N0": "N0 zero noise",
    "N1": "N1 uniform (device medians)",
    "N2": "N2 real (converted snapshot)",
    "N3-qubit": "N3 planted readout fault",
    "N3-connection": "N3 planted CX fault",
}
VARIANT_TITLES = {
    "L1-cx-min": "CX duration = device minimum",
    "L1-cx-max": "CX duration = device maximum",
    "L1-reset-readout": "reset error = readout error",
}
DEVICE_ORDER = ["jakarta", "hanoi"]
CIRCUIT_ORDER = ["A1", "A3", "A5", "B", "C", "D5", "D7"]
CONDITION_ORDER = ["N0", "N1", "N2", "N3-qubit", "N3-connection"]

# Reference categorical slots 1-3 (dataviz skill palette, validated all-pairs) + neutral.
COLOR_OTHER = "#8c8b86"
COLOR_CHOSEN = "#2a78d6"
COLOR_FIXED = "#eb6834"


def fmt(x: float, digits: int = 3) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    if x == 0:
        return "0"
    if abs(x) >= 1e-2 and abs(x) < 1e3:
        return (
            f"{x:.{digits + 1}f}".rstrip("0").rstrip(".") if abs(x) >= 1 else f"{x:.{digits + 1}f}"
        )
    return f"{x:.{digits - 1}e}"


def fi(i: Interval, digits: int = 3) -> str:
    return f"{fmt(i.estimate, digits)} [{fmt(i.low, digits)}, {fmt(i.high, digits)}]"


def pct(i: Interval) -> str:
    if math.isnan(i.estimate):
        return "n/a"
    lo = "n/a" if math.isnan(i.low) else f"{100 * i.low:+.1f}%"
    hi = "n/a" if math.isnan(i.high) else f"{100 * i.high:+.1f}%"
    return f"{100 * i.estimate:+.1f}% [{lo}, {hi}]"


def mapping_text(m: Sequence[int]) -> str:
    return "(" + ",".join(str(q) for q in m) + ")"


def key(s: CaseSummary) -> tuple[int, int, int, str]:
    c = s.case
    return (
        DEVICE_ORDER.index(c["device"]),
        CIRCUIT_ORDER.index(c["circuit"]["label"]),
        CONDITION_ORDER.index(c["condition"]),
        c["variant"],
    )


def base_cases(summaries: dict[str, CaseSummary]) -> list[CaseSummary]:
    return sorted((s for s in summaries.values() if s.case["variant"] == "base"), key=key)


def find(
    summaries: dict[str, CaseSummary],
    device: str,
    label: str,
    condition: str,
    variant: str = "base",
) -> CaseSummary | None:
    suffix = "" if variant == "base" else f"-{variant}"
    return summaries.get(f"{device}-{label}-{condition}{suffix}")


def comparison_cell(c: Comparison) -> str:
    return f"**{c.verdict}**; Δ {fi(c.absolute)}; rel {pct(c.relative)}"


def plot_case(summary: CaseSummary, out: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    metric = summary.metric
    rows = sorted(
        enumerate(summary.placements),
        key=lambda t: (t[1].p_l.estimate if metric == "P_L" else t[1].detection.estimate, t[0]),
    )
    xs = list(range(1, len(rows) + 1))
    est, lo, hi, colors = [], [], [], []
    for _, p in rows:
        i = p.p_l if metric == "P_L" else p.detection
        est.append(i.estimate)
        lo.append(i.estimate - i.low)
        hi.append(i.high - i.estimate)
        colors.append(COLOR_CHOSEN if p.is_chosen else COLOR_FIXED if p.is_fixed else COLOR_OTHER)
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=130)
    for x, e, l_, h, col in zip(xs, est, lo, hi, colors, strict=True):
        if col == COLOR_OTHER:
            ax.errorbar(x, e, yerr=[[l_], [h]], fmt="o", ms=3, color=col, elinewidth=1, capsize=0)
    labels_done = set()
    for (_, p), x, e, l_, h in zip(rows, xs, est, lo, hi, strict=True):
        for flag, color, label, marker in (
            (p.is_fixed, COLOR_FIXED, "fixed baseline", "s"),
            (p.is_chosen, COLOR_CHOSEN, "chosen (noise-aware)", "D"),
        ):
            if flag:
                ax.errorbar(
                    x,
                    e,
                    yerr=[[l_], [h]],
                    fmt=marker,
                    ms=8,
                    color=color,
                    elinewidth=2,
                    capsize=3,
                    label=None if label in labels_done else label,
                    markeredgecolor="white",
                    markeredgewidth=1.5,
                    zorder=3,
                )
                labels_done.add(label)
    ax.plot([], [], "o", ms=3, color=COLOR_OTHER, label="other valid placements")
    c = summary.case
    ylabel = "logical error per shot, P_L" if metric == "P_L" else "detection-event rate (M3)"
    ax.set_ylabel(ylabel + " (95% interval)")
    ax.set_xlabel("valid placements, sorted by " + ("P_L" if metric == "P_L" else "M3"))
    ax.set_title(
        f"{DEVICE_TITLES[c['device']]}, circuit {CIRCUIT_TITLES[c['circuit']['label']]}, "
        "N2 (under this noise model)",
        fontsize=10,
    )
    ax.grid(axis="y", color="#e4e3df", linewidth=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)


def git_commit_of(path: str) -> str | None:
    try:
        out = (
            subprocess.run(
                ["git", "log", "--diff-filter=A", "--format=%H %cI", "--", path],
                capture_output=True,
                text=True,
                check=True,
            )
            .stdout.strip()
            .splitlines()
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out[-1] if out else None


def build_report(summaries: dict[str, CaseSummary], figures_dir: Path, repo_root: Path) -> str:
    lines: list[str] = []
    w = lines.append
    cases = base_cases(summaries)
    n2 = [s for s in cases if s.case["condition"] == "N2"]
    total_seconds = sum(s.wall_clock_seconds for s in summaries.values())
    any_raw = next(iter(summaries.values())).raw
    prov = any_raw["provenance"]

    w("# Noise-aware placement for Nove: v1 results report")
    w("")
    w(
        "Every result in this report holds **under this noise model**: the Pauli-channel "
        "model converted from the `qiskit-ibm-runtime` 0.50.0 FakeJakartaV2 and FakeHanoiV2 "
        "snapshot files by the SPEC.md conversion rules, simulated with Nove's Stim backend. "
        "Nothing here is a hardware measurement or a claim about any real device (X8)."
    )
    w("")
    w(
        "Generated by `python -m noise_aware_compilation.report` from the results files in "
        "`results/runs/`. Reproduce everything with the single command in README.md."
    )
    w("")

    # ---------------------------------------------------------------- headline
    w("## 1. Headline (N2, real converted noise)")
    w("")
    w(
        "Chosen = the noise-aware placement. Fixed = the first valid placement in lexicographic "
        "order (R-B1). Average = mean P_L over every valid placement (R-B2). A verdict is "
        "*better* or *worse* only when the two 95% intervals do not overlap; overlapping "
        "intervals are *indistinguishable* (R-R2). Δ is chosen minus reference (negative means "
        "the chosen placement has the lower P_L), with its 95% interval; rel is Δ relative to "
        "the reference."
    )
    w("")
    w("| Device | Circuit | Chosen P_L | Fixed P_L | Average P_L | vs fixed | vs average |")
    w("|---|---|---|---|---|---|---|")
    for s in n2:
        c = s.case
        ch = s.placements[s.chosen_index]
        fx = s.placements[s.fixed_index]
        metric_note = ""
        chv = ch.p_l if s.metric == "P_L" else ch.detection
        fxv = fx.p_l if s.metric == "P_L" else fx.detection
        w(
            f"| {DEVICE_TITLES[c['device']]} | {CIRCUIT_TITLES[c['circuit']['label']]}{metric_note} "
            f"| {fi(chv)} | {fi(fxv)} | {fi(s.average)} | {comparison_cell(s.vs_fixed)} "
            f"| {comparison_cell(s.vs_average)} |"
        )
    w("")
    w(
        "For circuit C (distance 2) the code detects but cannot correct errors, so its rows "
        "compare the detection-event rate M3 instead of P_L; lower M3 is better."
    )
    w("")

    # ---------------------------------------------------------------- method
    w("## 2. Method")
    w("")
    sel = any_raw["selection"]
    w(
        f"**Selection score.** {sel['score_method']} Lower is better. The score is computed from "
        "the circuit, the device and the noise model only; no sampler is built and no candidate's "
        "logical error rate is estimated (R-P4, tested by T-C8)."
    )
    w("")
    w(f"**Tie-break.** {sel['tie_break_rule']}")
    w("")
    dec = any_raw["decoder"]
    w(
        "**Decoder (R-R6).** PyMatching "
        f"{prov['pymatching_version']}, built separately for every placement from that placement's "
        f"own detector error model: {dec['construction']}. Default PyMatching settings otherwise "
        "(no custom weights or edge merging options). **The decoder's model is approximate**: "
        f'{dec["reason"]} Every results file records `decoder.decoder_model = "approximate"`.'
    )
    w("")
    w(
        "**Simulation.** Each placement's noiseless circuit is relabelled onto physical qubits, "
        "lowered with Nove's calibrated `HardwareNoiseModel` against the converted `DeviceModel`, "
        f"and sampled with Nove {prov['nove_version']} (commit `{prov['nove_commit']}`) using "
        f'`backend="stim"` (Stim {prov["stim_version"]}). P_L is the fraction of shots in which '
        "the decoder's observable prediction is wrong; intervals are Wilson 95%. ε (M2) uses "
        "ε = ½[1 − (1 − 2P_L)^(1/r)], applied to the interval ends. M3 is the fraction of "
        "detectors that fire, with a normal 95% interval clustered by shot."
    )
    w("")
    cfg_rule = any_raw["shots"]
    w(
        "**Shots and seeds.** Three base seeds per case "
        f"({', '.join(str(x) for x in any_raw['seeds']['base_seeds'])}); every placement of a case "
        "gets the same number of shots per seed, starting at 200,000. Under N2 and N3 (and the "
        "T-L1 reruns of N2) the case's shots per seed are doubled for all placements together "
        "until every placement's Wilson half-width is at most 10% of its P_L, or 25.6 million "
        "shots per seed is reached (T-P4). Evaluation chunk seeds are "
        f"`{any_raw['seeds']['evaluation_seed_rule']}` with chunks of {cfg_rule['chunk_shots']:,} "
        "shots. Selection uses no seeds at all, so evaluation and selection seeds are disjoint "
        "(R-F3)."
    )
    w("")
    w(
        "**Comparisons.** Chosen vs fixed: Newcombe hybrid-score interval for the difference and a "
        "Katz log interval for the ratio. Chosen vs average: the exact variance of "
        "p_chosen − mean(p) under independent binomials, normal interval; relative difference by "
        "the delta method. Headroom: oracle best vs worst P_L with Wilson intervals (R-B3; the "
        "oracle is used only here, never for selection). M4: Spearman rank correlation between "
        "score and simulated P_L (positive means the score orders placements correctly, since "
        "both are lower-is-better). M5: the chosen placement's rank by simulated P_L (1 = best, "
        "ties share the average rank) and its regret, P_L(chosen) − P_L(oracle best), with a "
        "Newcombe interval. The full list of choices and reasons is in DECISIONS.md."
    )
    w("")

    # ---------------------------------------------------------------- per case tables
    w("## 3. All cases: comparisons, headroom, score validity and regret (R-R2, R-R3, R-R4)")
    w("")
    w(
        "Headroom column: oracle best and worst placements with 95% intervals; *placement does "
        "not matter* is stated when they overlap. M4 values that are negative are flagged ⚠ "
        "(T-P3). N0 has no errors at all, so M4 is undefined there."
    )
    w("")
    for device in DEVICE_ORDER:
        w(f"### {DEVICE_TITLES[device]}")
        w("")
        w(
            "| Circuit | Condition | Placements | Shots/seed | Chosen | vs fixed | vs average "
            "| Headroom: best / worst | M4 Spearman | M5 rank; regret |"
        )
        w("|---|---|---|---|---|---|---|---|---|---|")
        for s in cases:
            c = s.case
            if c["device"] != device:
                continue
            best = s.placements[s.best_index]
            worst = s.placements[s.worst_index]
            bi = best.p_l if s.metric == "P_L" else best.detection
            wi = worst.p_l if s.metric == "P_L" else worst.detection
            head = f"{fi(bi)} / {fi(wi)}"
            if s.headroom_overlap:
                head += "; *placement does not matter for this case*"
            m4 = "undefined" if math.isnan(s.spearman) else f"{s.spearman:+.3f}"
            if not math.isnan(s.spearman) and s.spearman < 0:
                m4 += " ⚠ negative"
            w(
                f"| {c['circuit']['label']}{' (M3)' if s.metric != 'P_L' else ''} "
                f"| {CONDITION_TITLES[c['condition']]} | {len(s.placements)} | {s.shots_per_seed:,} "
                f"| {mapping_text(s.placements[s.chosen_index].mapping)} "
                f"| {comparison_cell(s.vs_fixed)} | {comparison_cell(s.vs_average)} | {head} "
                f"| {m4} | {fmt(s.chosen_rank, 3)} of {len(s.placements)}; {fi(s.regret)} |"
            )
        w("")

    # ---------------------------------------------------------------- figures
    w("## 4. N2: all placements sorted by P_L (R-R5)")
    w("")
    for s in n2:
        c = s.case
        name = f"{c['device']}-{c['circuit']['label']}-N2.png"
        plot_case(s, figures_dir / name)
        rel = (figures_dir / name).relative_to(repo_root).as_posix()
        w(f"**{DEVICE_TITLES[c['device']]}, circuit {CIRCUIT_TITLES[c['circuit']['label']]}.**")
        w("")
        w(f"![{c['device']} {c['circuit']['label']} N2]({rel})")
        w("")

    # ---------------------------------------------------------------- T-P5
    w("## 5. Per-round logical error ε for circuit A at 1, 3 and 5 rounds (T-P5)")
    w("")
    w("| Device | Rounds | ε chosen | ε fixed | ε average | vs fixed | vs average |")
    w("|---|---|---|---|---|---|---|")
    from noise_aware_compilation.stats import epsilon_interval

    tp5: dict[str, list[str]] = {}
    for device in DEVICE_ORDER:
        for label, r in (("A1", 1), ("A3", 3), ("A5", 5)):
            sa = find(summaries, device, label, "N2")
            if sa is None:
                continue
            ch = sa.placements[sa.chosen_index]
            fx = sa.placements[sa.fixed_index]
            w(
                f"| {DEVICE_TITLES[device]} | {r} | {fi(ch.epsilon)} | {fi(fx.epsilon)} "
                f"| {fi(epsilon_interval(sa.average, r))} | {sa.vs_fixed.verdict} "
                f"| {sa.vs_average.verdict} |"
            )
            tp5.setdefault(device, []).append(f"{sa.vs_fixed.verdict}/{sa.vs_average.verdict}")
    w("")
    for device, verdicts in tp5.items():
        same = len(set(verdicts)) == 1
        w(
            f"- {DEVICE_TITLES[device]}: verdicts (vs fixed / vs average) at 1, 3, 5 rounds are "
            f"{', '.join(verdicts)}; the direction is "
            f"{'the same for all three' if same else '**not** the same for all three'}."
        )
    w("")

    # ---------------------------------------------------------------- T-P6
    w("## 6. Gain vs code distance on FakeHanoiV2 (T-P6)")
    w("")
    w("| Distance | Circuit | Chosen − average Δ P_L [95%] | Relative | Verdict |")
    w("|---|---|---|---|---|")
    diffs = []
    for label, d in (("A3", 3), ("D5", 5), ("D7", 7)):
        sd = find(summaries, "hanoi", label, "N2")
        if sd is None:
            continue
        diffs.append(sd.vs_average.absolute)
        w(
            f"| {d} | {label} | {fi(sd.vs_average.absolute)} | {pct(sd.vs_average.relative)} "
            f"| {sd.vs_average.verdict} |"
        )
    w("")
    if len(diffs) == 3:
        w(
            f"Trend of the chosen-vs-average difference from distance 3 to 7: **{trend(diffs)}** "
            "(rule in DECISIONS.md D10: *flat* when all three intervals overlap each other; "
            "otherwise *grows* or *shrinks* by the magnitude at d=7 relative to d=3)."
        )
    w("")

    # ---------------------------------------------------------------- T-F2
    w("## 7. Uniform-noise null (N1, T-F2)")
    w("")
    w(
        "Pearson chi-square test of homogeneity of P_L across all valid placements "
        "(k × 2 table of failures and successes, pooled over seeds). Pass criterion p > 0.01."
    )
    w("")
    w("| Device | Circuit | χ² | dof | p | vs fixed | vs average | Statement |")
    w("|---|---|---|---|---|---|---|---|")
    for s in cases:
        if s.case["condition"] != "N1":
            continue
        c = s.case
        h = s.homogeneity
        gain = (
            "no gain"
            if h.p_value > 0.01 or math.isnan(h.p_value)
            else "placements differ (homogeneity rejected)"
        )
        hp = "n/a (M3 only)" if math.isnan(h.p_value) else f"{h.p_value:.3f}"
        hs = "n/a" if math.isnan(h.statistic) else f"{h.statistic:.2f}"
        w(
            f"| {DEVICE_TITLES[c['device']]} | {c['circuit']['label']} | {hs} | {h.dof} | {hp} "
            f"| {s.vs_fixed.verdict} | {s.vs_average.verdict} | **{gain}** under N1 |"
        )
    w("")
    w(
        "Under N1 every placement receives identical noise, so all selection scores are equal and "
        "the tie-break picks the lexicographically first placement, which is the fixed baseline. "
        "The chosen placement therefore has no information advantage, and where the homogeneity "
        "test finds no difference between placements (p > 0.01) the report states **no gain** "
        "under uniform noise. A pairwise verdict other than *indistinguishable* in this table "
        "(chosen vs average) is then a chance fluctuation of that single placement: at 95% "
        "confidence about one comparison in twenty is expected to separate by chance, and the "
        "placement compared was fixed by the tie-break, not selected for its outcome. These "
        "pairwise verdicts are reported as computed and are not claimed as gains or losses (X8)."
    )
    w("")

    # ---------------------------------------------------------------- T-C4
    w("## 8. Planted faults (N3, T-C4)")
    w("")
    w(
        "A faulty placement uses the planted qubit (readout error × 5), or runs a CX on the "
        "planted connection (CX error × 5, both directions). The faults were recorded in "
        "`configs/benchmark.json` before running. Pass: the chosen placement avoids the fault, "
        "and its P_L interval is not entirely above any faulty placement's interval."
    )
    w("")
    w(
        "| Device | Circuit | Fault | Faulty placements | Chosen avoids | Chosen P_L | "
        "Best faulty P_L | Not higher than any faulty |"
    )
    w("|---|---|---|---|---|---|---|---|")
    for s in cases:
        c = s.case
        if not c["condition"].startswith("N3"):
            continue
        ch = s.placements[s.chosen_index]
        faulty = [p for p in s.placements if p.contains_fault]
        fault = s.raw["planted_fault"]
        desc = (
            f"qubit {fault['qubit']}"
            if fault["kind"] == "qubit"
            else f"connection {tuple(fault['connection'])}"
        )
        if s.metric == "P_L":
            ok = all(not (ch.p_l.low > f.p_l.high) for f in faulty)
            best_f = min(faulty, key=lambda p: p.p_l.estimate)
            chosen_cell, best_cell, ok_cell = fi(ch.p_l), fi(best_f.p_l), "yes" if ok else "**no**"
        else:
            ok = all(not (ch.detection.low > f.detection.high) for f in faulty)
            best_f = min(faulty, key=lambda p: p.detection.estimate)
            chosen_cell = "M3 " + fi(ch.detection)
            best_cell = "M3 " + fi(best_f.detection)
            ok_cell = ("yes" if ok else "**no**") + " (M3)"
        w(
            f"| {DEVICE_TITLES[c['device']]} | {c['circuit']['label']} | {desc} "
            f"| {len(faulty)} of {len(s.placements)} | {'yes' if not ch.contains_fault else '**no**'} "
            f"| {chosen_cell} | {best_cell} | {ok_cell} |"
        )
    w("")

    # ---------------------------------------------------------------- T-R2
    w("## 9. Seed consistency (T-R2) and precision (T-P4)")
    w("")
    w(
        "Verdicts recomputed from each base seed's shots alone (vs fixed / vs average). "
        "T-R2 requires the same verdict on all three seeds."
    )
    w("")
    w("| Case | Seed 1 | Seed 2 | Seed 3 | Same on all seeds |")
    w("|---|---|---|---|---|")
    for s in n2:
        seed_v = [f"{x['vs_fixed']} / {x['vs_average']}" for x in s.per_seed_verdicts]
        same = len(set(seed_v)) == 1
        w(f"| {s.case_id} | {' | '.join(seed_v)} | {'yes' if same else '**no**'} |")
    w("")
    w(
        "Precision (T-P4): for every placement under N2 and N3, the Wilson half-width must be at "
        "most 10% of P_L."
    )
    w("")
    w("| Case | Shots per seed | Placements meeting T-P4 |")
    w("|---|---|---|")
    for s in cases:
        if s.case["condition"] not in ("N2", "N3-qubit", "N3-connection"):
            continue
        n_ok = sum(precision_ok(p) for p in s.placements)
        mark = "" if n_ok == len(s.placements) else " **(unmet)**"
        w(f"| {s.case_id} | {s.shots_per_seed:,} | {n_ok} of {len(s.placements)}{mark} |")
    w("")

    # ---------------------------------------------------------------- T-L1
    w("## 10. Assumption sensitivity (T-L1, R-R7)")
    w("")
    w(
        "Each N2 case was rerun end to end (selection and evaluation) with the CX duration set "
        "to the device minimum, to the device maximum, and with reset error equal to each "
        "qubit's readout error. *Holds* means the verdict equals the N2 verdict."
    )
    w("")
    w("| Device | Circuit | Variant | Chosen | vs fixed | vs average | Verdicts hold |")
    w("|---|---|---|---|---|---|---|")
    for s in n2:
        c = s.case
        for variant, title in VARIANT_TITLES.items():
            v = find(summaries, c["device"], c["circuit"]["label"], "N2", variant)
            if v is None:
                w(
                    f"| {DEVICE_TITLES[c['device']]} | {c['circuit']['label']} | {title} | missing | | | |"
                )
                continue
            holds = (
                v.vs_fixed.verdict == s.vs_fixed.verdict
                and v.vs_average.verdict == s.vs_average.verdict
            )
            w(
                f"| {DEVICE_TITLES[c['device']]} | {c['circuit']['label']} | {title} "
                f"| {mapping_text(v.placements[v.chosen_index].mapping)} "
                f"| {comparison_cell(v.vs_fixed)} | {comparison_cell(v.vs_average)} "
                f"| {'yes' if holds else '**no**'} |"
            )
    w("")

    # ---------------------------------------------------------------- limitations
    w("## 11. Limitations (R-R8, T-L2)")
    w("")
    for item in LIMITATIONS:
        w(f"- {item}")
    w("")

    # ---------------------------------------------------------------- provenance
    w("## 12. Provenance and compute")
    w("")
    w(
        f"- Nove {prov['nove_version']}, commit `{prov['nove_commit']}`; Stim {prov['stim_version']}; "
        f"PyMatching {prov['pymatching_version']}; device files from qiskit-ibm-runtime "
        f"{prov['qiskit_ibm_runtime_version']} (not installed at runtime)."
    )
    for device in DEVICE_ORDER:
        first = next((x for x in cases if x.case["device"] == device), None)
        if first is None:
            continue
        p = first.raw["provenance"]
        files = ", ".join(f"`{k}` {v[:16]}…" for k, v in p["source_files"].items())
        w(
            f"- {DEVICE_TITLES[device]}: source files {files}; calibration snapshot "
            f"`{p['calibration_snapshot_sha256'][:16]}…`. Full hashes are in every results file."
        )
    w(f"- Results files: {len(summaries)} in `results/runs/` (one per run).")
    w(
        f"- Total compute (sum of per-run wall-clock times): **{total_seconds / 3600:.2f} hours** "
        f"({total_seconds:,.0f} s) on {any_raw['workers']} worker processes."
    )
    total_shots = sum(p.shots for s in summaries.values() for p in s.placements)
    w(f"- Total simulated shots: {total_shots:,}.")
    cfg_commit = git_commit_of("configs/benchmark.json")
    if cfg_commit:
        w(f"- Benchmark config and fixed baseline first committed in `{cfg_commit}` (R-F4).")
    w("")

    # ---------------------------------------------------------------- appendix
    w("## Appendix: M1-M3 for every device, circuit, noise condition and placement (R-R1)")
    w("")
    w(
        "P_L and ε are pooled over the three seeds; intervals are 95%. ★ chosen, ◆ fixed, "
        "✗ contains the planted fault."
    )
    w("")
    for s in cases:
        c = s.case
        w(
            f"### {DEVICE_TITLES[c['device']]} / {CIRCUIT_TITLES[c['circuit']['label']]} / "
            f"{CONDITION_TITLES[c['condition']]}"
        )
        w("")
        w("| Placement | Score | Shots | P_L (M1) | ε (M2) | Detection rate (M3) |")
        w("|---|---|---|---|---|---|")
        for p in sorted(s.placements, key=lambda p: p.mapping):
            tag = (
                ("★" if p.is_chosen else "")
                + ("◆" if p.is_fixed else "")
                + ("✗" if p.contains_fault else "")
            )
            w(
                f"| {mapping_text(p.mapping)}{tag} | {p.score:.5f} | {p.shots:,} | {fi(p.p_l)} "
                f"| {fi(p.epsilon)} | {fi(p.detection)} |"
            )
        w("")
    return "\n".join(lines) + "\n"


LIMITATIONS = [
    "**Results hold under this noise model only.** Both devices are `qiskit-ibm-runtime` 0.50.0 "
    "fake-backend snapshots converted by fixed rules into a Pauli model; no hardware was run "
    "and nothing here is a claim about hardware (X8).",
    "**K1 – approximate decoder model.** Nove's `Experiment.to_detector_model()` raises a raw "
    "Stim `ValueError` when calibrated two-qubit noise has more than one non-zero Pauli term, "
    "so no exact detector error model exists for these experiments. The decoder (and the "
    "selection score) use Stim's `approximate_disjoint_errors` model, so decoding is not "
    "matched exactly to the simulated noise.",
    "**K2 – CX timing per gate name.** Nove sets durations per gate name, not per qubit pair, "
    "so the per-pair CX duration differences (235–540 ns on FakeJakartaV2, 181–729 ns on "
    "FakeHanoiV2) are replaced by the device mean. T-L1 brackets this with the device minimum "
    "and maximum.",
    "**K3 – symmetric readout.** Nove's readout error is symmetric; the snapshots' asymmetric "
    "readout (prob_meas0_prep1 vs prob_meas1_prep0) is represented by their average, "
    "`readout_error`.",
    "**K4 – directional gate noise.** CX(a,b) and CX(b,a) are separate entries with their own "
    "calibrated error. Circuit B uses CX ancilla→data (the reverse of circuit A), so it "
    "experiences the opposite-direction entries.",
    "**K5 – idle noise up to the highest index.** Nove applies idle noise to every qubit index "
    "up to the highest one used, including unused qubits; this does not change P_L (unused "
    "qubits are never measured) but means the lowered circuits differ in size by placement.",
    "**K6 – Pauli approximation.** Noise is a stochastic Pauli-channel approximation; coherent "
    "errors, crosstalk, leakage and drift are not modelled.",
    "**K7 – snapshot dates.** The FakeJakartaV2 snapshot is dated 2024-05-27, after "
    "ibmq_jakarta's retirement on 2023-09-26; the FakeHanoiV2 snapshot is dated 2025-02-26. "
    "Both are realistic noise profiles, not live-device measurements.",
    "**K8 – no surface code.** Both devices have at most 3 neighbours per qubit, so no surface "
    "code fits SWAP-free; v1 covers repetition codes only.",
    "**Conversion departures forced by the data (DECISIONS.md D3, D4).** On FakeHanoiV2 two "
    "directed CX entries (CX(19,20) and CX(5,8)) have `gate_error` = 1, for which the rule "
    "1.25·e/15 per term sums to 1.25 > 1; these are capped at 1/15 per term (total 1). Five "
    "FakeHanoiV2 qubits (2, 5, 10, 11, 13) have T2 > 2·T1, for which the rule gives a negative "
    "Z idle rate that Nove rejects; it is clamped to 0. These are reported departures from the "
    "conversion rules, not silent changes.",
    "**Reset error is 0 in the main runs** because the snapshots carry no reset error; T-L1 "
    "reruns N2 with reset error = readout error.",
    "**Selection score is a heuristic.** It is the expected number of detector-visible faults "
    "per shot, which ignores how faults combine into logical errors; M4 measures how well it "
    "orders placements.",
]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write REPORT.md from results files")
    parser.add_argument("--runs", type=Path, default=Path("results/runs"))
    parser.add_argument("--out", type=Path, default=Path("REPORT.md"))
    parser.add_argument("--figures", type=Path, default=Path("results/figures"))
    parser.add_argument("--summary-json", type=Path, default=Path("results/summary.json"))
    args = parser.parse_args(argv)
    summaries = load_summaries(args.runs)
    root = args.out.resolve().parent
    text = build_report(summaries, args.figures.resolve(), root)
    args.out.write_text(text, encoding="utf-8")
    args.summary_json.write_text(
        json.dumps(summary_json(summaries), indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {args.out} ({len(summaries)} runs)")
    return 0


def summary_json(summaries: dict[str, CaseSummary]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for cid, s in summaries.items():
        out[cid] = {
            "metric": s.metric,
            "chosen": list(s.placements[s.chosen_index].mapping),
            "fixed": list(s.placements[s.fixed_index].mapping),
            "vs_fixed": {
                "verdict": s.vs_fixed.verdict,
                "absolute": s.vs_fixed.absolute.to_dict(),
                "relative": s.vs_fixed.relative.to_dict(),
            },
            "vs_average": {
                "verdict": s.vs_average.verdict,
                "absolute": s.vs_average.absolute.to_dict(),
                "relative": s.vs_average.relative.to_dict(),
            },
            "average": s.average.to_dict(),
            "headroom_overlap": s.headroom_overlap,
            "spearman": s.spearman,
            "chosen_rank": s.chosen_rank,
            "regret": s.regret.to_dict(),
            "homogeneity_p": s.homogeneity.p_value,
            "per_seed_verdicts": s.per_seed_verdicts,
            "shots_per_seed": s.shots_per_seed,
            "wall_clock_seconds": s.wall_clock_seconds,
        }
    return out


if __name__ == "__main__":
    raise SystemExit(main())
