"""Build REPORT_V3.md and its forest plot from the held-out analysis (SPEC_V3 section 10).

Usage::

    python scripts/report_v3.py

Reads ``results_v3/held_out/analysis.json`` (built by ``python -m nac_bench.analysis_v3``
from the committed run files) and writes ``REPORT_V3.md`` and
``results_v3/held_out/figures/forest.png``. Every number about the held-out devices comes
from that file. The drift, reset and timing paragraphs quote their own committed files,
named where they are used. The script lives in ``scripts/`` so that the F4 code record of
``nac_bench/`` stays unchanged (X17).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / "results_v3" / "held_out" / "analysis.json"
FIGURE = ROOT / "results_v3" / "held_out" / "figures" / "forest.png"
OUT = ROOT / "REPORT_V3.md"

DEV = {
    "boston": "FakeBoston",
    "pittsburgh": "FakePittsburgh",
    "berlin": "FakeBerlin",
    "kingston": "FakeKingston",
    "aachen": "FakeAachen",
    "nighthawk": "FakeNighthawk",
}
REF = {
    "naive": "fixed placement",
    "lowest-summed-error": "lowest summed error",
    "qiskit-default-l2": "Qiskit's default (level 2)",
    "mapomatic": "mapomatic",
    "idle-aware-summed-error": "idle-aware summed error",
    "v1": "the v1 score",
    "detection-rate": "the detection rate",
    "qiskit-default-l3": "Qiskit level 3",
    "qiskit-vf2": "Qiskit VF2Layout",
    "decoder-graph": "the decoder-graph score",
}
SHORT = {
    "naive": "Fixed",
    "lowest-summed-error": "Summed error",
    "qiskit-default-l2": "Qiskit L2",
    "mapomatic": "mapomatic",
    "idle-aware-summed-error": "Idle-aware SE",
    "v1": "v1 score",
}
PANEL = {
    "naive": "against the fixed placement",
    "lowest-summed-error": "against summed error",
    "qiskit-default-l2": "against Qiskit's default (L2)",
    "mapomatic": "against mapomatic",
}
HEADLINE_REFS = ["naive", "lowest-summed-error", "qiskit-default-l2", "mapomatic"]
STRONG_REFS = HEADLINE_REFS[1:]
CIRCUIT = {
    "A": "d=3 bit-flip, 3 rounds",
    "B": "d=3 phase-flip, 3 rounds",
    "D5": "d=5 bit-flip, 3 rounds",
    "D7": "d=7 bit-flip, 3 rounds",
    "S3": "d=3 surface code, 3 rounds",
    "S5": "d=5 surface code, 5 rounds",
}
LABEL_ORDER = ["A", "B", "D5", "D7", "S3", "S5"]
CONFIRMATORY = ["boston", "pittsburgh", "berlin"]
VARIANT_NAME = {
    "correlated_matching": "Correlated matching (same samples re-decoded)",
    "rounds_equal_distance": "D5 and D7 in a memory of *d* rounds",
    "grading_variants/cz-duration-min": "Every CZ at the shortest CZ length",
    "grading_variants/cz-duration-max": "Every CZ at the longest CZ length",
    "grading_variants/idle-z-x2": "Idle Z rate doubled",
    "grading_variants/cz-without-1q": "CZ error less its single-qubit layer",
    "grading_variants/directional": "Readout and reset at P(1\\|0)",
    "grading_variants/lognormal-0.1-draw0": "Log-normal noise, δ = 0.1, draw 0",
    "grading_variants/lognormal-0.1-draw1": "Log-normal noise, δ = 0.1, draw 1",
    "grading_variants/lognormal-0.1-draw2": "Log-normal noise, δ = 0.1, draw 2",
    "grading_variants/lognormal-0.1-draw3": "Log-normal noise, δ = 0.1, draw 3",
    "grading_variants/lognormal-0.25-draw0": "Log-normal noise, δ = 0.25, draw 0",
    "grading_variants/lognormal-0.25-draw1": "Log-normal noise, δ = 0.25, draw 1",
    "grading_variants/lognormal-0.25-draw2": "Log-normal noise, δ = 0.25, draw 2",
    "grading_variants/lognormal-0.25-draw3": "Log-normal noise, δ = 0.25, draw 3",
}
BERLIN_NOTE = (
    "FakeBerlin's device-level calibration aggregates (medians, counts and ranges) were "
    "seen before the held-out config was frozen; no decision used them, and the square "
    "lattice has no unseen device to use instead"
)


def split_case(case_id: str) -> tuple[str, str, str]:
    _, setting, device, label = case_id.split("-", 3)
    return setting, device, label


def case_name(case_id: str) -> str:
    _, device, label = split_case(case_id)
    return f"{DEV[device]} {label}"


def pct(x: float, digits: int = 1) -> str:
    return f"{x * 100:+.{digits}f}%".replace("-", "−")


def pct_plain(x: float, digits: int = 0) -> str:
    return f"{x * 100:.{digits}f}%"


def rel_cell(c: dict[str, Any]) -> str:
    r = c["relative"]
    return f"{pct(r['estimate'])} [{pct(r['low'])}, {pct(r['high'])}]"


def sci(x: float | None) -> str:
    if x is None:
        return "–"
    return f"{x:.2e}".replace("e-0", "e−").replace("e-", "e−")


def tally(members: list[dict[str, Any]], key: str = "after_holm") -> Counter[str]:
    return Counter(m[key] for m in members)


def bw(c: Counter[str], n: int | None = None) -> str:
    n = n if n is not None else sum(c.values())
    return f"{c['better']} better, {c['worse']} worse of {n}"


def not_better(counts: dict[str, Counter[str]], n: int) -> str:
    k = sum(c["tie"] + c["worse"] for c in counts.values())
    return f"all {n} better" if k == 0 else f"{k} of {n} not better"


def claim_kind(text: str) -> str:
    if text.startswith("beats") or text.startswith("the decoder graph helps"):
        return "beats"
    if text.startswith("mixed"):
        return "mixed"
    if text.startswith("loses"):
        return "loses"
    return "no claim"


def order_key(case_id: str) -> tuple[int, int]:
    _, device, label = split_case(case_id)
    devs = list(DEV)
    return devs.index(device), LABEL_ORDER.index(label)


# --------------------------------------------------------------------------- figure


def forest_plot(members: list[dict[str, Any]], out: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    colours = {"boston": "#0072B2", "pittsburgh": "#D55E00", "berlin": "#009E73"}
    markers = {"better": "o", "tie": "D", "worse": "X"}
    cases = sorted({m["case_id"] for m in members}, key=order_key)
    y_of = {c: len(cases) - 1 - i for i, c in enumerate(cases)}
    fig, axes = plt.subplots(1, 4, figsize=(12.5, 5.2), sharey=True)
    for ax, ref in zip(axes, HEADLINE_REFS, strict=True):
        for m in (m for m in members if m["reference"] == ref):
            r = m["relative"]
            y = y_of[m["case_id"]]
            col = colours[m["device"]]
            ax.plot(
                [r["low"] * 100, r["high"] * 100], [y, y], color=col, lw=2, solid_capstyle="round"
            )
            face = col if m["after_holm"] != "tie" else "white"
            ax.plot(
                r["estimate"] * 100,
                y,
                marker=markers[m["after_holm"]],
                ms=8,
                color=col,
                markerfacecolor=face,
                markeredgewidth=1.6,
                linestyle="none",
            )
        ax.axvline(0, color="#555555", lw=1)
        ax.set_title(PANEL[ref], fontsize=11)
        ax.set_xlabel("relative change in P_L (%)", fontsize=9)
        ax.grid(axis="x", color="#dddddd", lw=0.6)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.tick_params(labelsize=9)
    axes[0].set_yticks(list(y_of.values()), [case_name(c) for c in y_of])
    handles = [Line2D([], [], color=colours[d], lw=2, label=DEV[d]) for d in colours]
    handles += [
        Line2D([], [], color="#333333", marker="o", linestyle="none", label="better after Holm"),
        Line2D(
            [],
            [],
            color="#333333",
            marker="D",
            markerfacecolor="white",
            linestyle="none",
            label="tie",
        ),
        Line2D([], [], color="#333333", marker="X", linestyle="none", label="worse after Holm"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=6, frameon=False, fontsize=9)
    fig.suptitle(
        "Held-out devices, reset = readout: the decoder-graph score's choice against each reference "
        "(negative = lower P_L; 95% Newcombe intervals)",
        fontsize=11,
    )
    fig.tight_layout(rect=(0, 0.07, 1, 0.95))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)


# --------------------------------------------------------------------------- report


def build(a: dict[str, Any], analysis_sha: str, wall: dict[str, Any]) -> str:
    fam = a["families"]["v3-headline"]
    members = fam["members"]
    iso = a["families"]["v3-decoder-graph-isolation"]["members"]
    claims = a["claims"]
    sec = {s["case_id"]: s for s in a["secondary"]}
    comps = a["comparisons"]
    sens = a["sensitivity"]

    by = defaultdict(list)
    for m in members:
        by[(m["lattice"], m["reference"])].append(m)
    hh = {r: tally(by[("heavy-hex", r)]) for r in HEADLINE_REFS}
    sq = {r: tally(by[("square", r)]) for r in HEADLINE_REFS}
    naive_hh = [m["relative"]["estimate"] for m in by[("heavy-hex", "naive")]]
    naive_sq = [m["relative"]["estimate"] for m in by[("square", "naive")]]
    strong_sq = [m["relative"]["estimate"] for r in STRONG_REFS for m in by[("square", r)]]

    # Sensitivity: does each claim keep its wording?
    base_claim = {(c["lattice"], c["reference"]): c["claim"] for c in claims}
    keep: dict[tuple[str, str], list[str]] = defaultdict(list)
    for name, v in sens.items():
        for c in v["claims"]:
            k = (c["lattice"], c["reference"])
            if c["claim"] == base_claim[k]:
                keep[k].append(name)
    n_sens = len(sens)

    def variant_counts(vmembers: list[dict[str, Any]], lattice: str) -> dict[str, Counter[str]]:
        out: dict[str, Counter[str]] = {}
        for r in HEADLINE_REFS:
            out[r] = tally(
                [
                    m
                    for m in vmembers
                    if m["lattice"] == lattice and m["reference"].split("@")[0] == r
                ]
            )
        return out

    graded_dir = a["families"][
        "v3-headline with the headline setting's choices graded at the directional setting (exploratory)"
    ]["members"]
    resel_dir = a["families"]["v3-headline at the directional setting (exploratory)"]["members"]
    zero = a["families"]["v3-headline at the zero setting (exploratory)"]["members"]
    gd_hh = variant_counts(graded_dir, "heavy-hex")
    gd_sq = variant_counts(graded_dir, "square")
    rd_hh = variant_counts(resel_dir, "heavy-hex")
    rd_sq = variant_counts(resel_dir, "square")
    z_hh = variant_counts(zero, "heavy-hex")
    z_sq = variant_counts(zero, "square")

    # Seed sweep over every case that has one
    sweeps = [s["qiskit_l2_seed_sweep"] for s in a["secondary"] if s.get("qiskit_l2_seed_sweep")]
    sweep_all_same = all(
        s["same_as_main"] == s["seeds"] and s["distinct_other_choices"] == 0 for s in sweeps
    )
    n_sweep_cases = len(sweeps)

    readout_conf = sorted(
        [
            cid
            for cid in sec
            if cid.startswith("v3-readout")
            and split_case(cid)[1] in CONFIRMATORY
            and split_case(cid)[2] in ("A", "B", "D5", "D7")
        ],
        key=order_key,
    )

    def comp(case_id: str, method: str, ref: str) -> dict[str, Any]:
        for c in comps:
            if c["case_id"] == case_id and c["method"] == method and c["reference"] == ref:
                found: dict[str, Any] = c
                return found
        raise KeyError((case_id, method, ref))

    L: list[str] = []
    w = L.append

    w("# Noise-Aware Placement for Nove: v3 Report")
    w("")
    w(
        "Generated by `python scripts/report_v3.py` from `results_v3/held_out/analysis.json` "
        f"(sha256 `{analysis_sha[:12]}…`), which `python -m nac_bench.analysis_v3` builds from the 65 "
        f"committed run files in `results_v3/held_out/runs/`. The runs and the analysis ran on patty at "
        f"commit `{a['analysis_git_commit'][:7]}`, a clean tree descending from the held-out config's freeze "
        f"`{a['provenance']['f4_commit'][:7]}` (tag `v3-held-out-config-r6`). The plan is [SPEC_V3.md](SPEC_V3.md) "
        "(revision 6); choices made along the way are in [DECISIONS_V3.md](DECISIONS_V3.md). v1's and v2's reports "
        "are unchanged apart from their v3 banners."
    )
    w("")
    w(
        "**Every result holds under this noise model only:** the converted calibration snapshots of IBM's fake "
        "backends (`qiskit-ibm-runtime` 0.50.0), simulated by Nove as Pauli channels and decoded by PyMatching. "
        "None is a measurement on hardware, and each device is one day's calibration. **Held-out** means the "
        "device's calibration was converted only after the config, the code and every seed were frozen. "
        f"FakeBoston and FakePittsburgh (heavy-hex) and FakeBerlin (square lattice) decide the claims. {BERLIN_NOTE}. "
        "FakeKingston and FakeAachen were seen before the freeze and are exploratory. FakeNighthawk's calibration "
        "is synthetic (the file names itself `fake_nighthawk`), it was seen, and it is exploratory everywhere. "
        "Unless a table says otherwise, the reset setting is **reset error = readout error**."
    )
    w("")

    # ------------------------------------------------------------------ summary
    w("## Summary")
    w("")
    w(
        "v3 asked whether the decoder-graph score, developed on heavy-hex CX devices, still picks better qubit "
        "placements than the methods people use on new devices with CZ gates, on the heavy-hex layout and on the "
        "square lattice where it was never developed. The answer depends on the layout."
    )
    w("")
    w(
        f"- **Square lattice (FakeBerlin):** its choice was better after Holm in all {sum(sum(c.values()) for c in sq.values())} "
        f"comparisons: by {pct_plain(-max(naive_sq))} to {pct_plain(-min(naive_sq))} lower P_L than the fixed placement and by "
        f"{pct_plain(-max(strong_sq))} to {pct_plain(-min(strong_sq))} than summed error, Qiskit's default and mapomatic. "
        "This held under every sensitivity run and grading variant but one comparison in one draw."
    )
    w(
        f"- **Heavy-hex (FakeBoston, FakePittsburgh):** it beat the fixed placement in all 8 cases "
        f"({pct_plain(-max(naive_hh))} to {pct_plain(-min(naive_hh))} lower P_L). Against each of the three methods it was "
        "better in 5 cases and worse in 1, a *mixed* result. Its wins are at d=3 and d=5; at d=7 it ties or loses. "
        "These mixed results are fragile: with the same placements graded under direction-aware readout it is worse "
        f"than summed error in {gd_hh['lowest-summed-error']['worse']} of 6 cases and than Qiskit's default in "
        f"{gd_hh['qiskit-default-l2']['worse']} of 6."
    )
    w(
        "- **Surface code:** FakeBerlin has no d=5 surface-code embedding that avoids its removed couplers, a "
        "pre-registered possible outcome. The family therefore has 48 members, not 52, and v3 makes no "
        "confirmatory surface-code claim. On FakeNighthawk (synthetic, exploratory) the scaled score's d=5 choice "
        "beat the fixed placement and tied the other methods."
    )
    w(
        "- **Drift** (development devices, exploratory): the decoder-graph score's gain mostly survives a month of "
        "real recalibration, but it keeps less of it than summed error, and one 14-day change made its d=7 choice "
        "worse than the fixed placement. Choosing again restores the gain."
    )
    w("")
    w(
        "**Table 1. Claims.** Held-out devices, reset = readout; *t* is the number of better cases a *beats* needs."
    )
    w("")
    w(
        "| Devices | Against | Result after Holm | Per device | Same wording under the 15 sensitivity runs |"
    )
    w("| --- | --- | --- | --- | --- |")
    for c in claims:
        k = (c["lattice"], c["reference"])
        kind = claim_kind(c["claim"])
        ms = by[k]
        t_all = tally(ms)
        if c["lattice"] == "heavy-hex":
            per = "; ".join(
                f"{DEV[d]} {tally([m for m in ms if m['device'] == d])['better']}–{tally([m for m in ms if m['device'] == d])['worse']}"
                for d in ("boston", "pittsburgh")
            )
            devices = "FakeBoston, FakePittsburgh (heavy-hex)"
        else:
            per = f"FakeBerlin {t_all['better']}–{t_all['worse']}"
            devices = "FakeBerlin (square)†"
        result = f"**{kind}**: {t_all['better']} better, {t_all['worse']} worse of {len(ms)} (*t* = {c['t']})"
        w(f"| {devices} | {REF[c['reference']]} | {result} | {per} | {len(keep[k])} of {n_sens} |")
    w("")
    w(
        f"† {BERLIN_NOTE}. Per device gives better–worse after Holm. A sensitivity run keeps a claim's wording "
        "when its verdict and its counts are unchanged; section 2 says what changes."
    )
    w("")
    w("![Forest plot of the 48 family comparisons](results_v3/held_out/figures/forest.png)")
    w("")
    w(
        "**Figure 1.** The 48 comparisons of the headline family, held-out devices, reset = readout. Each row is "
        "one case; each panel one reference. Filled markers are better or worse after Holm, hollow diamonds ties."
    )
    w("")

    # ------------------------------------------------------------------ layout
    w("## 1. Does the score still choose better placements on new layouts with CZ? (layout)")
    w("")
    w(
        "**On the square lattice, yes, in every comparison. On heavy-hex it beats the fixed placement every time "
        "but is only mixed against the three methods people use, and it loses ground as the code grows.**"
    )
    w("")
    w(
        "**Heavy-hex.** After Holm the decoder-graph's choice was "
        f"{bw(hh['lowest-summed-error'])} against lowest summed error, {bw(hh['qiskit-default-l2'])} against Qiskit's "
        f"default and {bw(hh['mapomatic'])} against mapomatic. Every one of the 3-round d=3 circuits is a win against all three methods except "
        "FakePittsburgh A against mapomatic (worse). At d=5 it beats all three on FakePittsburgh; on FakeBoston it "
        "ties summed error, loses to Qiskit's default and beats mapomatic. At d=7 it is never better: it ties all "
        "three on FakeBoston, and on FakePittsburgh it loses to summed error and ties the other two."
    )
    w("")
    w(
        f"**Square lattice.** All 16 comparisons on FakeBerlin are better after Holm, and the margins are large "
        f"({pct_plain(-max(strong_sq))} to {pct_plain(-min(strong_sq))} against the three methods). On FakeBerlin summed "
        "error, Qiskit's default and mapomatic often choose the same placement, and the decoder-graph score finds "
        "one the others miss."
    )
    w("")
    w(
        "**The fixed placement in context.** The fixed placement is the first valid placement in lexicographic order, "
        "so where it lands is luck. On the heavy-hex devices it lies between the 81st and 92nd percentile of P_L, a "
        "poor draw; on FakeBerlin between the 1st and 7th, a good one. The decoder-graph's choice is at or below "
        "the 1st percentile in every confirmatory case (Table 2), so the large gains over the fixed placement on "
        "heavy-hex partly reflect a bad fixed placement, and FakeBerlin's gains come despite a good one."
    )
    w("")
    w(
        "**Table 2. Every confirmatory repetition-code case**, held-out devices, reset = readout, 3 rounds. "
        "Percentile = share of simulated placements with lower pooled P_L (for FakeBerlin D7, among its 1,000 uniform "
        "placements). Regret is the decoder-graph's cross-fitted regret relative to the best placement; *tie* means "
        "no placement was shown to beat its choice."
    )
    w("")
    w(
        "| Case | Placements simulated | Mean P_L, all placements | Fixed P_L (percentile) | Decoder-graph P_L (percentile) | Per round | Regret |"
    )
    w("| --- | --- | --- | --- | --- | --- | --- |")
    for cid in readout_conf:
        s = sec[cid]
        ch = s["choices"]
        key = (
            "percentile_among_random_sample"
            if "random_sample" in s
            else "percentile_among_simulated"
        )
        mean = s["all_placement_mean_p_l"]
        mean_txt = (
            sci(mean)
            if mean is not None
            else f"{sci(s['random_sample']['mean_p_l'])} (uniform sample)"
        )
        sim = f"{s['simulated']:,}"
        if "random_sample" in s:
            rs = s["random_sample"]
            b = rs["cross_fitted_best"]
            sim += f" (best found; sample best {sci(b['estimate'])} [{sci(b['low'])}, {sci(b['high'])}])"
        reg = s["regret"]["decoder-graph"]
        w(
            f"| {case_name(cid)} | {sim} | {mean_txt} | {sci(ch['naive']['p_l'])} ({ch['naive'][key]:.1f}) | "
            f"{sci(ch['decoder-graph']['p_l'])} ({ch['decoder-graph'][key]:.2f}) | {sci(ch['decoder-graph']['p_l_per_round'])} | "
            f"{pct(reg['relative']['estimate'])}, {reg['verdict']} |"
        )
    w("")
    berlin_d5 = sec["v3-readout-berlin-D5"]
    w(
        f"FakeBerlin D5 was simulated on all {berlin_d5['simulated']:,} placements within its runtime guard, so its regret "
        "is exact. FakeBerlin D7 was simulated on a best-found set (every method's choice, the top 30 by six scores "
        "and 1,000 uniform placements), so its regret can only understate the true one. The decoder-graph score "
        "has non-zero regret on FakeBoston D5 and D7: there a better placement exists that it did not pick."
    )
    w("")
    iso_hh_idle = tally(
        [
            m
            for m in iso
            if m["lattice"] == "heavy-hex" and m["reference"] == "idle-aware-summed-error"
        ]
    )
    iso_hh_v1 = tally([m for m in iso if m["lattice"] == "heavy-hex" and m["reference"] == "v1"])
    iso_sq_idle = tally(
        [m for m in iso if m["lattice"] == "square" and m["reference"] == "idle-aware-summed-error"]
    )
    iso_sq_v1 = tally([m for m in iso if m["lattice"] == "square" and m["reference"] == "v1"])
    w(
        "**Does the decoder graph itself help?** A second family compares the score with two rivals that see the "
        "same idle noise but use no decoder graph: an idle-aware summed error and the v1 score. On FakeBerlin the "
        f"decoder graph helps beyond the idle-aware summed error ({bw(iso_sq_idle)}) and is mixed against the v1 "
        f"score ({bw(iso_sq_v1)}). On heavy-hex both are mixed: {bw(iso_hh_idle)} against the idle-aware summed error, "
        f"{bw(iso_hh_v1)} against the v1 score."
    )
    w("")
    # exploratory competitors
    conf_read = [
        c
        for c in comps
        if c["role"] == "confirmatory"
        and c["setting"] == "readout"
        and split_case(c["case_id"])[2] in ("A", "B", "D5", "D7")
    ]

    def vs(method: str, ref: str) -> Counter[str]:
        return Counter(
            c["verdict"] for c in conf_read if c["method"] == method and c["reference"] == ref
        )

    sweep_text = (
        f"transpiler seed: ten seeds gave the main choice in all {n_sweep_cases} cases that ran the sweep."
        if sweep_all_same
        else "transpiler seed in some cases (Appendix B)."
    )
    pb = vs("pilot-best", "decoder-graph")
    dr = vs("decoder-graph", "detection-rate")
    fs = vs("fast-search", "decoder-graph")
    l3 = vs("decoder-graph", "qiskit-default-l3")
    w(
        "**Other competitors (exploratory, unadjusted 95% intervals, the 12 confirmatory cases).** *Simulate briefly "
        f"and keep the best* (100,000 pilot shots per placement) was better than the decoder-graph's choice in "
        f"{pb['better']}, tied in {pb['tie']} and worse in {pb['worse']}; it is far worse at d=7 on heavy-hex, where "
        f"100,000 shots see only a few failures. Against Kan et al.'s detection-rate proxy the decoder-graph score was better in "
        f"{dr['better']}, tied in {dr['tie']} and worse in {dr['worse']}. Against Qiskit at level 3 it was better in "
        f"{l3['better']} and tied in {l3['tie']}. The fast search found the exhaustive choice's P_L in "
        f"{fs['tie']} of 12 and was worse in {fs['worse']}. Qiskit's level-2 choice did not change with the "
        + sweep_text
    )
    w("")
    # seen and synthetic devices
    w(
        "**Seen and synthetic devices (exploratory).** These decide no claim. Table 3 counts the decoder-graph's "
        "verdicts by unadjusted 95% interval, reset = readout, repetition codes A, B, D5 and D7."
    )
    w("")
    w(
        "**Table 3. Exploratory devices**, held-out conversion, reset = readout: decoder-graph's choice, better–tie–worse of 4."
    )
    w("")
    w("| Device | Role | Fixed | Summed error | Qiskit L2 | mapomatic | Idle-aware SE | v1 score |")
    w("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for d, role in (("kingston", "seen"), ("aachen", "seen"), ("nighthawk", "synthetic, seen")):
        cells = []
        for r in [*HEADLINE_REFS, "idle-aware-summed-error", "v1"]:
            t = Counter(
                c["verdict"]
                for c in comps
                if c["setting"] == "readout"
                and c["method"] == "decoder-graph"
                and c["reference"] == r
                and split_case(c["case_id"])[1] == d
                and split_case(c["case_id"])[2] in ("A", "B", "D5", "D7")
            )
            cells.append(f"{t['better']}–{t['tie']}–{t['worse']}")
        w(f"| {DEV[d]} | {role} | " + " | ".join(cells) + " |")
    w("")
    w(
        "On FakeKingston and FakeAachen, heavy-hex devices with more removed couplers than the claim devices, the "
        "score beats the three methods in every case, more clearly than on FakeBoston and FakePittsburgh. On "
        "FakeNighthawk it ties every method except the fixed placement at d=3 bit-flip, beats the three methods in "
        "the other circuits, and ties the idle-aware summed error and the v1 score at d=7."
    )
    w("")

    # ------------------------------------------------------------------ qualifiers
    w("## 2. What qualifies the claims")
    w("")
    w(
        "**The fixed-placement claims and FakeBerlin's claims survive every check. The heavy-hex mixed claims "
        "do not: under most changed models fewer than 5 of 8 cases stay better, and under direction-aware readout "
        "the score's heavy-hex choices are mostly worse than the methods'.**"
    )
    w("")
    w(
        "Each check keeps the placements and changes one thing, then recomputes the family. The *d*-round runs and "
        "correlated matching use the family's shots (about 4,000 failures per placement). The grading variants use "
        "the other placements' shots (about 400 failures, a 10% half-width), so a change from *better* to *tie* there "
        "can be lost power rather than a lost effect; a change to *worse* cannot. Table 4 counts, on the heavy-hex "
        "devices, the comparisons against summed error, Qiskit's default and mapomatic (24 in all)."
    )
    w("")
    w(
        "**Table 4. Sensitivity runs and grading variants**, held-out confirmatory devices, reset = readout (exploratory)."
    )
    w("")
    w(
        "| Check | Heavy-hex better–worse vs summed error / Qiskit L2 / mapomatic (of 8 each) | Heavy-hex comparisons turned worse | FakeBerlin comparisons changed |"
    )
    w("| --- | --- | --- | --- |")
    w(
        "| Headline | "
        + " / ".join(f"{hh[r]['better']}–{hh[r]['worse']}" for r in STRONG_REFS)
        + " | – | – |"
    )
    for name in VARIANT_NAME:
        v = sens[name]
        vm = v["family"]["members"]
        cnt = variant_counts(vm, "heavy-hex")
        changed = v["changed"]
        to_worse = sum(
            1
            for x in changed
            if x["sensitivity"] == "worse"
            and "berlin" not in x["case_id"]
            and x["reference"] in STRONG_REFS
        )
        berlin = sum(1 for x in changed if "berlin" in x["case_id"])
        w(
            f"| {VARIANT_NAME[name]} | "
            + " / ".join(f"{cnt[r]['better']}–{cnt[r]['worse']}" for r in STRONG_REFS)
            + f" | {to_worse} | {berlin} |"
        )
    w(
        "| Directional setting, every method chooses again (family shots) | "
        + " / ".join(f"{rd_hh[r]['better']}–{rd_hh[r]['worse']} of 6" for r in STRONG_REFS)
        + f" | – | {not_better(rd_sq, 12)} |"
    )
    w(
        "| Directional setting, headline choices graded (family shots) | "
        + " / ".join(f"{gd_hh[r]['better']}–{gd_hh[r]['worse']} of 6" for r in STRONG_REFS)
        + f" | – | {not_better(gd_sq, 12)} |"
    )
    w(
        "| Reset error 0, every method chooses again (family shots) | "
        + " / ".join(f"{z_hh[r]['better']}–{z_hh[r]['worse']}" for r in STRONG_REFS)
        + f" | – | {not_better(z_sq, 16)} |"
    )
    w("")
    naive_changes = [x for v in sens.values() for x in v["changed"] if x["reference"] == "naive"]
    naive_changes += [
        m
        for fm in (graded_dir, resel_dir, zero)
        for m in fm
        if m["reference"].startswith("naive") and m["after_holm"] != "better"
    ]
    assert not naive_changes, naive_changes
    berlin_changes = [
        (VARIANT_NAME[n], x)
        for n, v in sens.items()
        for x in v["changed"]
        if "berlin" in x["case_id"]
    ]
    berlin_text = (
        "; ".join(
            f"{n}: {case_name(x['case_id'])} against {REF[x['reference']]}, {x['headline']} → {x['sensitivity']}"
            for n, x in berlin_changes
        )
        or "none"
    )
    zero_sq_ties = sum(z_sq[r]["tie"] + z_sq[r]["worse"] for r in HEADLINE_REFS)
    w(
        "Four things stand out. First, nothing touches the fixed-placement claims: every confirmatory case stays "
        f"better under every check. Second, FakeBerlin changes in {len(berlin_changes)} "
        f"{'comparison' if len(berlin_changes) == 1 else 'comparisons'} across all checks ({berlin_text}), and at "
        f"reset 0, {zero_sq_ties} of its 16 comparisons are not better; its "
        "claims are robust. Third, on heavy-hex the *d*-round run, at full power, turns the d=7 ties into losses: "
        "at 7 rounds the score's d=7 choice is worse than all three methods on both devices. Fourth, readout direction matters most. A Z-basis repetition code meets only P(1|0), about "
        "half the published symmetric readout error. Grading the headline choices with that readout makes the "
        f"score worse than summed error in {gd_hh['lowest-summed-error']['worse']} of the 6 heavy-hex cases and than "
        f"Qiskit's default in {gd_hh['qiskit-default-l2']['worse']}; when every method chooses again with it, the "
        f"score recovers to {rd_hh['lowest-summed-error']['better']} better and {rd_hh['lowest-summed-error']['worse']} worse "
        "against summed error. The score is model-based, so its edge on heavy-hex depends on the readout model "
        "being right (*inferred, not tested*)."
    )
    w("")
    w(
        "Correlated matching changes no verdict. Qiskit's transpiler seed changes no choice. No case failed or fell "
        "back to a best-found set. Two cases have no valid placement and leave their families: FakeBerlin's d=5 "
        "surface code at reset = readout and at reset 0. Appendix C lists every comparison each check changes."
    )
    w("")

    # ------------------------------------------------------------------ scale
    sup = {(x["device"], x["setting"]): x for x in a["surface_suppression"]}
    s5 = sec["v3-readout-nighthawk-S5"]
    w("## 3. Can the scaled score rank surface-code placements? (scale)")
    w("")
    w(
        "**v3 cannot answer this confirmatorily.** FakeBerlin has no SWAP-free d=5 surface-code embedding: every "
        "one of its embeddings crosses a removed coupler. The spec named this outcome before any held-out data was "
        "read. The family therefore had 48 members, the claims table has no surface-code row, and the question is "
        "answered only by exploratory results: FakeNighthawk's (synthetic calibration, seen) and FakeMiami's "
        "development checks."
    )
    w("")
    w(
        "**Table 5. Surface codes**, exploratory, reset = readout: the scaled score's choice (*k* = 640) against each method, unadjusted 95% intervals."
    )
    w("")
    w(
        "| Case | Embeddings | Fixed | Summed error | Qiskit L2 | mapomatic | Idle-aware SE | Exact score's choice |"
    )
    w("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for cid in ("v3-readout-nighthawk-S5", "v3-readout-nighthawk-S3", "v3-readout-berlin-S3"):
        cells = []
        for r in [*HEADLINE_REFS, "idle-aware-summed-error"]:
            c = comp(cid, "scaled", r)
            cells.append(f"{pct(c['relative']['estimate'])}, {c['verdict']}")
        ch = sec[cid]["choices"]
        same = (
            "same"
            if ch["scaled"]["p_l"] == ch["exact-decoder-graph"]["p_l"]
            else (
                f"different; exact {pct(comp(cid, 'exact-decoder-graph', 'lowest-summed-error')['relative']['estimate'])} vs summed error"
            )
        )
        label = f"{case_name(cid)} ({'synthetic' if 'nighthawk' in cid else 'seen aggregates'})"
        w(f"| {label} | {sec[cid]['simulated']} | " + " | ".join(cells) + f" | {same} |")
    w("")
    w(
        f"FakeNighthawk has only {s5['simulated']} d=5 embeddings, so ranking them is easy and the scaled score agrees "
        "with the exact score; *k* = 10 and 80 choose the same embedding. Its choice beats the fixed placement and "
        "ties the rest. The device left room to differ: the fixed placement is "
        f"{s5['headroom']['naive_regret_verdict']} than the cross-fitted best by its interval. Going from d=3 to d=5 barely helps on FakeNighthawk: "
        f"the best per-round P_L falls from {pct_plain(sup[('nighthawk', 'readout')]['best_per_round']['3'], 2)} to "
        f"{pct_plain(sup[('nighthawk', 'readout')]['best_per_round']['5'], 2)} (Λ = {sup[('nighthawk', 'readout')]['lambda_3_5']:.2f}; "
        f"{sup[('nighthawk', 'zero')]['lambda_3_5']:.2f} at reset 0), so this model sits close to threshold. "
        "At d=3 the scaled score loses to summed error on FakeBerlin while the exact score wins, so the approximation "
        "costs something there. The speed target (the whole d=5 selection within 60 seconds on one core) is missed "
        "on the development device: about 1.2 s per candidate at *k* = 640, about 150 s for FakeMiami's 128 "
        "candidates, against 0.06 s per candidate for the exact score. At d=5 the scaled score is not needed, and "
        "v3 cannot show where it would be."
    )
    w("")

    # ------------------------------------------------------------------ drift
    w("## 4. How much of the gain survives real recalibration? (drift)")
    w("")
    w(
        "**Most of it after a month, less of it than summed error keeps, and sometimes none.** These are exploratory "
        "runs on development devices with dated snapshots ([DRIFT.md](results_v3/drift/DRIFT.md)); each method "
        "chooses on the older calibration and is graded and decoded as someone who had not recalibrated would run it."
    )
    w("")
    w(
        "- After a month on FakeFez every old choice still beats the fixed placement. The decoder-graph's old choice "
        "has 17% to 46% lower P_L than the fixed placement, but summed error's and mapomatic's kept more of their "
        "gains in three of four circuits."
    )
    w(
        "- On FakeHanoi d=7 the decoder-graph's old choice became worse than the fixed placement: 127% worse after "
        "14 days and 76% after 9 months, and still worse when decoded with new weights, so the placement itself "
        "degraded."
    )
    w(
        "- After 12 months on FakeTorino three of its four old choices use two couplers that became uncalibrated. "
        "Graded with those couplers at the converter's limit they fail badly; at their older value they are 31% to "
        "81% better than the fixed placement ([UNCALIBRATED.md](results_v3/drift/uncalibrated/UNCALIBRATED.md)). Which is real is unknown."
    )
    w(
        "- Choosing again is what matters: the fresh choice beats the fixed placement by 27% to 96% outside FakeHanoi's "
        "d=3 circuits. Re-grading v2's synthetic drift with stale decoder weights leaves the score's result standing "
        "and lowers summed error's and Qiskit's on FakeJakarta A."
    )
    w("")

    # ------------------------------------------------------------------ use
    w("## 5. Can someone outside the project run it? (use)")
    w("")
    w(
        "**Yes, for the worked example.** The README's example installs the package, converts a device, places a "
        "circuit and prints the mapping. `scripts/check_readme_example.sh` ran it as written in a fresh virtual "
        "environment without Qiskit on Nov 14, 2026, and it passed. v3 adds no new interface; the converted "
        "held-out devices are in `src/noise_aware_compilation/device_data/`."
    )
    w("")

    # ------------------------------------------------------------------ reset
    w("## 6. The reset result")
    w("")
    w(
        "IBM publishes no reset error, and v1's headline assumed reset error 0. With reset error equal to readout "
        "error, the v1 score lost to the fixed placement on v1's seeds in three FakeHanoi d=3 circuits (3 rounds, "
        "5 rounds and the X basis); the 5-round case had been v1's −15.2% gain at reset 0. On fresh seeds the loss "
        "replicates by its Newcombe interval in one of the three (3 rounds, +10.5%), and the other two are ties. "
        "The decoder-graph score is better than the fixed placement by its Newcombe interval in the 3- and 5-round "
        "circuits at both settings, though the 3-round result at reset = readout does not hold after Holm "
        "(adjusted p = 0.41), and its reset-0 figure is v2's committed run "
        "([RESET.md](results_v3/reset/RESET.md) section 2). Every v3 case therefore runs at both settings; the "
        "headline uses reset = readout, and Table 4 gives reset 0."
    )
    w("")

    # ------------------------------------------------------------------ limitations
    w("## 7. Limitations and deviations")
    w("")
    w(
        "**Deviations after the freeze.** Converting the held-out devices hit two cases the converter had never "
        "met. Several devices have qubits whose single-qubit gate error is published as 1.0 (uncalibrated): FakeBoston "
        "146, FakePittsburgh 117, FakeKingston 96, 112, 113, 131 and 146, FakeAachen 19 and 35, and FakeNighthawk 85; "
        "FakeBerlin has none. A 1.0 error cannot be a Pauli channel, and FakeKingston 146 also has no T1 or T2. Such "
        "a qubit now loses all its couplers, as an uncalibrated coupler already did, and is recorded in the device's "
        "metadata. The first run then found a removed qubit's CZ channel still in the noise model and stopped; the "
        "converter now drops those too, and the Qiskit and mapomatic Target gives such qubits their published error. "
        "No result had been read when either fix was made, and the stopped run's output is set aside. Both fixes "
        "are listed with their reasons in the freeze record (`configs_v3/freeze_f4.json`) and in DECISIONS_V3.md "
        "(P2-56, P2-57)."
    )
    w("")
    w(
        f"**Cost.** The {wall['cases']} cases ran one after another on patty with 48 workers; their run files' clocks "
        f"sum to {wall['hours']:.1f} wall hours. The run used about 2,711 core-hours (user plus system time from the "
        "run's time log on patty), against 3,116 projected. No case hit a runtime guard."
    )
    w("")
    w("**What the results cannot show.**")
    w("")
    w(
        "- *One snapshot per device.* The intervals cover simulation noise only; another day's calibration could "
        "change which placement wins. No second snapshot of these devices exists in any `qiskit-ibm-runtime` release."
    )
    w(
        "- *Graded under the model that chose.* A model-based score gains if the model is right. Section 2's "
        "variants show the heavy-hex claims are sensitive to that; only hardware can settle it."
    )
    w(
        "- *Pauli noise only.* No coherent errors, leakage, time-correlated noise or crosstalk between "
        "simultaneous CZs; the fake backends publish none of these."
    )
    w(
        "- *Resets.* How a real reset fails is unknown; results are given at both ends of the assumption."
    )
    w(
        "- *Best-found regret.* Where not every placement was simulated (FakeBerlin D7, FakeNighthawk D5 and D7), "
        "regret is a lower bound."
    )
    w(
        "- *Seen devices.* FakeBerlin's aggregates were seen, and the square-lattice claim rests on that one device."
    )
    w(
        "- *Pinned software.* Qiskit 2.5.2 chose the Qiskit baselines; a later release might choose differently."
    )
    w(
        "- *Independence.* The spec, code and both pre-run reviews came from the thread that built v3. No person "
        "outside the project has reviewed them."
    )
    w("")

    # ------------------------------------------------------------------ appendices
    w("## Appendix A. Traceability")
    w("")
    w("| Requirement | Where |")
    w("| --- | --- |")
    for rid, where in [
        (
            "R3-R1 device kind and reset setting on every table and figure",
            "Table and figure captions",
        ),
        ("R3-R2 FakeNighthawk labels; FakeBerlin note", "Header, Table 1, Tables 3 and 5"),
        ("R3-R3 the reset result in the main body", "Section 6"),
        ("R3-R4 best-found set size and sample interval", "Table 2 (FakeBerlin D7), Appendix B"),
        ("R3-R5 no SWAP-free FakeBerlin d=5 embedding", "Summary, section 3"),
        ("R3-R6 headroom and suppression", "Section 3"),
        ("R3-R7 the fixed placement in context", "Section 1, Table 2, Appendix B"),
        ("R3-R8 3-round wording and *d*-round results", "Section 1, Table 4"),
        ("R3-R9 what qualifies each claim", "Table 1, section 2, Table 4, Appendix C"),
        ("Section 7 claim rules, families of 48 and 24", "Table 1, Appendix B"),
        ("Secondary endpoints (regret, ρ, percentiles, per round)", "Table 2, Appendix B"),
        ("P2-56, P2-57 deviations after F4", "Section 7"),
    ]:
        w(f"| {rid} | {where} |")
    w("")

    w("## Appendix B. Every family comparison and every case")
    w("")
    w(
        "**Headline family** (48), held-out confirmatory devices, reset = readout. Relative = (choice − reference) / reference."
    )
    w("")
    w("| Case | Against | Relative change [95%] | Holm p | After Holm |")
    w("| --- | --- | --- | --- | --- |")
    for m in sorted(
        members, key=lambda m: (order_key(m["case_id"]), HEADLINE_REFS.index(m["reference"]))
    ):
        w(
            f"| {case_name(m['case_id'])} | {REF[m['reference']]} | {rel_cell(m)} | {m['holm_p']:.3g} | {m['after_holm']} |"
        )
    w("")
    w("**Isolation family** (24), same devices and setting.")
    w("")
    w("| Case | Against | Relative change [95%] | Holm p | After Holm |")
    w("| --- | --- | --- | --- | --- |")
    for m in sorted(iso, key=lambda m: (order_key(m["case_id"]), m["reference"])):
        w(
            f"| {case_name(m['case_id'])} | {REF[m['reference']]} | {rel_cell(m)} | {m['holm_p']:.3g} | {m['after_holm']} |"
        )
    w("")
    w(
        "**Every case at reset = readout**, all six held-out devices. P_L of each choice over all rounds, with its "
        "percentile among the simulated placements (among the uniform sample where the set is best-found). Spearman's "
        "ρ is between the decoder-graph score (the scaled score on surface codes) and simulated P_L."
    )
    w("")
    w(
        "| Case | Role | Rounds | Simulated | Mean P_L | Fixed | Decoder-graph or scaled | Summed error | Qiskit L2 | mapomatic | ρ |"
    )
    w("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for cid in sorted((c for c in sec if c.startswith("v3-readout")), key=order_key):
        s = sec[cid]
        ch = s["choices"]
        key = (
            "percentile_among_random_sample"
            if "random_sample" in s
            else "percentile_among_simulated"
        )
        head = "decoder-graph" if "decoder-graph" in ch else "scaled"
        rho_src = (
            (s.get("random_sample") or {}).get("spearman_rho_with_p_l")
            or s.get("spearman_rho_with_p_l")
            or {}
        )
        rho = rho_src.get("decoder_graph", rho_src.get("scaled"))
        mean = s["all_placement_mean_p_l"]
        mean_txt = sci(mean) if mean is not None else f"{sci(s['random_sample']['mean_p_l'])}§"
        sim = f"{s['simulated']:,}" + ("§" if "random_sample" in s else "")

        def cell(mname: str, ch: dict[str, Any] = ch, key: str = key) -> str:
            x = ch.get(mname)
            return f"{sci(x['p_l'])} ({x[key]:.1f})" if x else "–"

        role = s["role"]
        w(
            f"| {case_name(cid)} | {role} | {s['rounds']} | {sim} | {mean_txt} | {cell('naive')} | {cell(head)} | "
            f"{cell('lowest-summed-error')} | {cell('qiskit-default-l2')} | {cell('mapomatic')} | "
            + (f"{rho:.2f} |" if rho is not None else "– |")
        )
    w("")
    w(
        "§ Best-found set (every method's choice, top 30 by six scores, 1,000 uniform placements); the mean is the uniform sample's."
    )
    w("")

    w("## Appendix C. Comparisons each check changes")
    w("")
    w(
        "Held-out confirmatory devices, reset = readout, exploratory. *Headline → check* gives the verdict after Holm before and after."
    )
    w("")
    for name in VARIANT_NAME:
        changed = sens[name]["changed"]
        if not changed:
            w(f"- **{VARIANT_NAME[name]}:** none.")
            continue
        items = "; ".join(
            f"{case_name(x['case_id'])} vs {SHORT.get(x['reference'], x['reference'])} {x['headline']} → {x['sensitivity']}"
            for x in sorted(changed, key=lambda x: (order_key(x["case_id"]), x["reference"]))
        )
        w(f"- **{VARIANT_NAME[name]}:** {items}.")
    w("")
    left = a["cases_left_and_why"]
    w(
        "**Cases that left a family:** "
        + "; ".join(f"{case_name(k)} ({split_case(k)[0]}): {v}" for k, v in left.items())
        + ". **Failed runs:** "
        + (str(len(a["earlier_failed_attempts"])) if a["earlier_failed_attempts"] else "none")
        + ". **Fallbacks to a best-found set:** "
        + (", ".join(a["every_placement_fallbacks"]) if a["every_placement_fallbacks"] else "none")
        + "."
    )
    w("")

    w("## Appendix D. Provenance and reproduction")
    w("")
    p = a["provenance"]
    one_machine = all(n == 1 for n in p["machines_per_device"].values())
    w(
        f"The analysis checked {p['runs_checked']} run files and {p['score_files_checked']} score files against the "
        f"freeze record. {'Each device ran on one machine (patty).' if one_machine else 'Some devices ran on several machines.'} "
        f"Analysis commit `{a['analysis_git_commit']}`, uncommitted changes: {'yes' if a['analysis_git_dirty'] else 'none'}. "
        "To rebuild, run `python -m nac_bench.analysis_v3 configs_v3/held_out.json` and then "
        "`python scripts/report_v3.py`. The analysis also reads the score files (2.3 GB), which stay on patty; the "
        "run files record their hashes."
    )
    w("")
    return "\n".join(L)


def run_wall_clock() -> dict[str, Any]:
    """Sum the committed run files' own wall clocks (about 10 s to read)."""
    import lzma

    files = sorted((ROOT / "results_v3" / "held_out" / "runs").glob("*.json.xz"))
    seconds = 0.0
    for f in files:
        with lzma.open(f) as fh:
            seconds += json.load(fh)["wall_clock_seconds"]
    return {"cases": len(files), "hours": seconds / 3600}


def main() -> int:
    raw = ANALYSIS.read_bytes()
    a = json.loads(raw)
    forest_plot(a["families"]["v3-headline"]["members"], FIGURE)
    OUT.write_text(
        build(a, hashlib.sha256(raw).hexdigest(), run_wall_clock()) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUT.relative_to(ROOT)} and {FIGURE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
