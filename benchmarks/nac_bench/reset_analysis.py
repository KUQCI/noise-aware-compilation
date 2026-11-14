"""Analysis of the v3 reset run: every method at reset error 0 and at reset error = readout error.

Usage::

    python -m nac_bench.reset_analysis configs_v3/reset_sensitivity.json

Reads ``results_v3/reset/runs`` (the reset = readout runs and the new A1/A5
runs at reset 0), v2's committed runs for the reset = 0 side of v2's cases and
Phase 1's Step 6b choices for them, and writes
``results_v3/reset/{reset.json,RESET.md}``. Verdicts follow P1-1, the Holm
family is the one the config pre-registered, and regret is cross-fitted
(P1-2). No placement is simulated here.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nac_bench import stats_v3
from nac_bench.analysis_v2 import CaseResult
from nac_bench.analysis_v2 import load as load_runs
from nac_bench.machine import machine
from nac_bench.reanalysis import _comparison_record, _fit_record, holm_families
from nac_bench.report_reanalysis import _minus, _p, _pct, _table
from nac_bench.run_reset import git_state

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "noise-aware-compilation.reset-analysis-v3/1"
SCORES = ("v1", "decoder-graph")
BASELINES = (
    "lowest-summed-error",
    "qiskit-vf2",
    "qiskit-default-l1",
    "qiskit-default-l2",
    "qiskit-default-l3",
    "mapomatic",
)
CONTEXT = ("qiskit-default-l1-published",)
NEW_METHODS = BASELINES[2:]
NAMES = {
    "v1": "v1 score",
    "decoder-graph": "decoder-graph score",
    "lowest-summed-error": "lowest summed error",
    "qiskit-vf2": "Qiskit VF2Layout (v2 target)",
    "qiskit-default-l1": "Qiskit, level 1",
    "qiskit-default-l2": "Qiskit, level 2 (the default)",
    "qiskit-default-l3": "Qiskit, level 3",
    "mapomatic": "mapomatic",
    "qiskit-default-l1-published": "Qiskit, level 1, published Target",
    "fixed": "naive placement",
    "average": "all-placement average",
}
SHORT = {
    "v1": "v1",
    "decoder-graph": "graph",
    "lowest-summed-error": "summed",
    "qiskit-vf2": "VF2 (v2)",
    "qiskit-default-l1": "Qiskit L1",
    "qiskit-default-l2": "Qiskit L2",
    "qiskit-default-l3": "Qiskit L3",
    "mapomatic": "mapomatic",
    "qiskit-default-l1-published": "Qiskit L1 pub.",
}
RUN_NAME = {"v3 reset run": "v3 (fresh seeds)", "v2 run": "v2 (v2 seeds)"}
DEVICE = {"jakarta": "FakeJakarta", "hanoi": "FakeHanoi", "algiers": "FakeAlgiers"}
LABEL = {
    "A1": "d=3, 1 round",
    "A": "d=3, 3 rounds",
    "A5": "d=3, 5 rounds",
    "B": "d=3 X",
    "D5": "d=5",
    "D7": "d=7",
}
ORDER = [
    (d, label)
    for d in ("jakarta", "hanoi", "algiers")
    for label in ("A1", "A", "A5", "B", "D5", "D7")
]
SETTING_NAME = {"zero": "reset 0", "readout": "reset = readout"}


def _key(device: str, label: str) -> str:
    return f"{device}-{label}"


def _picks_from(record: dict[str, Any], case: CaseResult, methods: Sequence[str]) -> dict[str, Any]:
    index = {p.mapping: i for i, p in enumerate(case.placements)}
    out: dict[str, int | None] = {}
    for m in methods:
        rec = record[m]
        mapping = rec["mapping"]
        if rec.get("failure") or mapping is None or not rec.get("swap_free", True):
            out[m] = None
        else:
            out[m] = index[tuple(mapping)]
    return out


def load_entries(config: dict[str, Any], runs_dir: Path) -> list[dict[str, Any]]:
    """One entry per (setting, device, circuit): its counts and every method's pick."""

    entries = []
    for cid, case in load_runs(runs_dir, config).items():
        raw = case.raw
        setting = raw["setting"]
        if raw["config_file_sha256"] != config["_config_sha256"]:
            raise RuntimeError(f"{cid} was run with another config")
        methods = NEW_METHODS + (CONTEXT if setting == "readout" else ())
        picks = dict(case.choices) | _picks_from(raw["baselines"], case, methods)
        entries.append(_entry(setting, case, picks, "v3 reset run"))

    v2_config = json.loads((ROOT / "configs_v2" / "benchmark_v2.json").read_text(encoding="utf-8"))
    step6b = json.loads(
        (ROOT / "results_v3" / "reanalysis" / "baselines.json").read_text(encoding="utf-8")
    )
    for cid, case in load_runs(ROOT / "results_v2" / "runs", v2_config).items():
        picks = dict(case.choices) | _picks_from(step6b["cases"][cid], case, NEW_METHODS)
        entries.append(_entry("zero", case, picks, "v2 run"))
    seen = {(e["setting"], e["key"]) for e in entries}
    if len(seen) != len(entries):
        raise RuntimeError("two runs for one setting and case")
    order = {k: i for i, k in enumerate(_key(d, label) for d, label in ORDER)}
    entries.sort(key=lambda e: (order[e["key"]], e["setting"] != "zero"))
    return entries


def _entry(setting: str, case: CaseResult, picks: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "setting": setting,
        "key": _key(case.device, case.label),
        "device": case.device,
        "label": case.label,
        "role": case.role,
        "case_id": case.case_id,
        "source": source,
        "case": case,
        "picks": picks,
    }


def comparisons(entries: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for e in entries:
        case, picks = e["case"], e["picks"]

        def add(
            method: str,
            reference: str,
            i: int,
            j: int | None,
            e: dict[str, Any] = e,
            case: CaseResult = case,
        ) -> None:
            row = _comparison_record(case, method, reference, i, j)
            row.update(
                {
                    "version": "v3-reset",
                    "variant": f"reset-{e['setting']}",
                    "setting": e["setting"],
                    "key": e["key"],
                    "source": e["source"],
                }
            )
            rows.append(row)

        for method, i in picks.items():
            if i is None:
                continue
            add(method, "fixed", i, case.fixed_index)
            if method in SCORES:
                add(method, "average", i, None)
                for baseline in BASELINES + CONTEXT:
                    j = picks.get(baseline)
                    if j is not None:
                        add(method, baseline, i, j)
    return rows


def choice_changes(entries: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by = {(e["setting"], e["key"]): e for e in entries}
    out: dict[str, Any] = {}
    for method in SCORES + BASELINES:
        changed, total = [], 0
        for _, key in [(s, k) for (s, k) in by if s == "readout"]:
            if ("zero", key) not in by:
                continue
            a, b = by[("zero", key)], by[("readout", key)]
            ia, ib = a["picks"].get(method), b["picks"].get(method)
            if ia is None or ib is None:
                continue
            total += 1
            ma = a["case"].placements[ia].mapping
            mb = b["case"].placements[ib].mapping
            if ma != mb:
                changed.append(key)
        out[method] = {"cases": total, "changed": changed}
    return out


def analyse(config: dict[str, Any], runs_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    entries = load_entries(config, runs_dir)
    rows = comparisons(entries)
    families = holm_families(rows, config)
    zero_family = {
        "alpha": config["alpha"],
        "families": {
            "reset-headline at reset 0": {
                "description": "The same 40 comparisons at reset 0 (Phase 1's v2-headline family, post hoc)",
                "select": {**config["families"]["reset-headline"]["select"], "setting": ["zero"]},
                "size": config["families"]["reset-headline"]["size"],
            }
        },
    }
    families.update(holm_families(rows, zero_family))
    fits = {
        f"{e['setting']}:{e['key']}": _fit_record(
            [list(p.per_seed) for p in e["case"].placements], e["picks"]
        )
        for e in entries
    }
    choices = {
        f"{e['setting']}:{e['key']}": {
            m: (None if i is None else list(e["case"].placements[i].mapping))
            for m, i in e["picks"].items()
        }
        for e in entries
    }
    result = {
        "schema": SCHEMA,
        "status": config["status"],
        "verdict_rule": stats_v3.VERDICT_RULE,
        "entries": [
            {k: v for k, v in e.items() if k not in ("case", "picks")}
            | {
                "num_placements": len(e["case"].placements),
                "shots_per_seed": e["case"].raw["shots"]["shots_per_seed"],
            }
            for e in entries
        ],
        "choices": choices,
        "comparisons": rows,
        "holm": families,
        "cross_fit": fits,
        "choice_changes": choice_changes(entries),
    }
    return result, entries


# --- report ----------------------------------------------------------------------------------


def _row(rows: Sequence[dict[str, Any]], **match: str) -> dict[str, Any] | None:
    for r in rows:
        if all(r.get(k) == v for k, v in match.items()):
            return r
    return None


def _cell(r: dict[str, Any] | None) -> str:
    if r is None:
        return "–"
    if r["same_placement"]:
        return "same placement"
    return f"{_pct(r['relative'])} {r['verdict']}"


def _short(r: dict[str, Any] | None) -> str:
    if r is None:
        return "–"
    if r["same_placement"]:
        return "same"
    e = 100 * r["relative"]["estimate"]
    return _minus(f"{e:+.1f}% {r['verdict']}")


def _case_name(device: str, label: str) -> str:
    held = " (held out)" if device == "algiers" else ""
    return f"{DEVICE[device]} {LABEL[label]}{held}"


def write_report(result: dict[str, Any], v1_rows: Sequence[dict[str, Any]], path: Path) -> Path:
    rows = result["comparisons"]
    prov = result["provenance"]
    lines: list[str] = []
    w = lines.append
    w("# Reset error = readout error: the v3 reset run")
    w("")
    w(
        "Generated by `python -m nac_bench.reset_analysis configs_v3/reset_sensitivity.json` from "
        "`results_v3/reset/runs`, v2's committed runs and Phase 1's Step 6b choices (code commit "
        f"`{str(prov['package_git_commit'])[:7]}`). **Status: pre-registered.** The config, its "
        "seeds and its Holm family were committed before any count of this run existed. Seen "
        "beforehand: every v1 and v2 result at reset 0 and v1's own reset variant (v1 score only). "
        "The devices are not new, so this tests v2's frozen decoder-graph score under a second noise "
        "setting, not on unseen hardware."
    )
    w("")
    w(
        "Every v1 and v2 headline used reset error 0, because IBM publishes no reset error. IBM "
        "resets a qubit by measuring it and flipping it on outcome 1, so a wrong readout leaves it "
        "in |1>: *reset = readout* sets each qubit's reset flip probability to its readout flip "
        "probability. Every method selects under the model it is graded in; Qiskit's transpile "
        "(levels 1 to 3) and mapomatic read an IBM-shaped Target whose reset carries the readout error. mapomatic's cost "
        "charges resets at the readout error in both settings, so its costs do not change; its pick "
        "between a placement and its mirror (an exact tie in its cost) can, because the level-3 "
        "transpile it starts from reads the reset error."
    )
    w("")
    w(
        f"**Verdict rule:** {result['verdict_rule']}. P_L is the logical error rate per shot; "
        "changes are relative to the reference, with 95% intervals. The naive placement is v1's and "
        "v2's *fixed* baseline, the first valid placement in lexicographic order. FakeAlgiers is "
        "v2's held-out device; FakeJakarta and FakeHanoi are development devices."
    )
    w("")

    fam = result["holm"]["reset-headline"]
    zero = result["holm"]["reset-headline at reset 0"]
    held = sum(1 for m in fam["members"] if m["holds_after_holm"])
    held0 = sum(1 for m in zero["members"] if m["holds_after_holm"])
    w("## 1. Does the decoder-graph score keep its headline at reset = readout?")
    w("")
    w(
        f"The pre-registered family is the {fam['description']}. **{held} of {fam['size']} comparisons "
        f"hold after Holm at α = {fam['alpha']} at reset = readout**, against {held0} of "
        f"{zero['size']} for the same comparisons at reset 0 (Phase 1, post hoc). A comparison "
        "holds when its verdict is better or worse and its Holm-adjusted p is below α. The "
        "family was registered with level 1 as Qiskit's default; the default is level 2 "
        "(`DECISIONS_V3.md` P2-26), which picks the same placement as level 1 in every case and "
        "setting here (`reset.json`, key `choices`)."
    )
    w("")
    counts: dict[str, dict[str, int]] = {}
    for m in fam["members"]:
        c = counts.setdefault(m["reference"], {"better": 0, "tie": 0, "worse": 0, "holds": 0})
        c[m["verdict"]] += 1
        c["holds"] += int(m["holds_after_holm"])
    w(
        _table(
            ["Reference", "better", "tie", "worse", "hold after Holm"],
            [
                [NAMES[ref], str(c["better"]), str(c["tie"]), str(c["worse"]), str(c["holds"])]
                for ref, c in counts.items()
            ],
        )
    )
    w("")
    table = []
    key_of = {r["case_id"]: r["key"] for r in rows}
    zero_by = {(key_of[m["case_id"]], m["reference"]): m for m in zero["members"]}
    for m in fam["members"]:
        r = _row(rows, case_id=m["case_id"], method="decoder-graph", reference=m["reference"])
        assert r is not None
        z = zero_by.get((r["key"], m["reference"]))
        table.append(
            [
                _case_name(r["device"], r["label"]),
                NAMES[m["reference"]],
                "same placement" if m["same_placement"] else _pct(m["relative"]),
                m["verdict"],
                _p(m["holm_p"]),
                "yes" if m["holds_after_holm"] else "no",
                "–" if z is None else ("same" if z["same_placement"] else _short(z)),
                "–" if z is None else ("yes" if z["holds_after_holm"] else "no"),
            ]
        )
    w(
        _table(
            [
                "Case",
                "Reference",
                "Change, reset = readout",
                "Verdict",
                "Holm p",
                "Holds",
                "Reset 0",
                "Held at reset 0",
            ],
            table,
        )
    )
    w("")

    w("## 2. The d=3 cases where v1's score lost at reset = readout")
    w("")
    w(
        "v1's run with reset = readout (REPORT.md section 10, v1's seeds) turned the v1 score's "
        "FakeHanoi d=3 comparisons against the naive placement into losses or ties. The v1 rows "
        "repeat those numbers under the difference rule. The v3 rows are this run's fresh seeds, "
        "with both scores; the v2 rows are v2's committed runs at reset 0 (3 rounds and X), "
        "which this run did not repeat."
    )
    w("")
    table = []
    for device in ("hanoi", "algiers"):
        for label in ("A1", "A", "A5", "B"):
            v1_label = {"A": "A3"}.get(label, label)
            if device == "hanoi":
                for variant, setting in (("base", "zero"), ("L1-reset-readout", "readout")):
                    r = _row(
                        v1_rows,
                        version="v1",
                        device=device,
                        label=v1_label,
                        condition="N2",
                        variant=variant,
                        reference="fixed",
                    )
                    table.append(
                        [
                            _case_name(device, label),
                            SETTING_NAME[setting],
                            "v1 (v1 seeds)",
                            _cell(r),
                            "–",
                        ]
                    )
            for setting in ("zero", "readout"):
                first = _row(rows, key=_key(device, label), setting=setting, reference="fixed")
                run = RUN_NAME[first["source"]] if first else "–"
                cells = [
                    _cell(
                        _row(
                            rows,
                            key=_key(device, label),
                            setting=setting,
                            method=m,
                            reference="fixed",
                        )
                    )
                    for m in SCORES
                ]
                table.append([_case_name(device, label), SETTING_NAME[setting], run, *cells])
    w(
        _table(
            ["Case", "Setting", "Run", "v1 score vs naive", "decoder-graph score vs naive"],
            table,
        )
    )
    w("")

    w("## 3. Every method against the naive placement")
    w("")
    w(
        "Change in P_L of each method's pick against the naive placement, with the verdict; "
        "intervals are in `reset.json`. *Qiskit L1 pub.* is Qiskit at level 1 on the Target as IBM "
        "publishes it (reset without an error), as context."
    )
    w("")
    methods = SCORES + BASELINES
    for setting in ("readout", "zero"):
        w(f"**{SETTING_NAME[setting].capitalize()}**")
        w("")
        cols = methods + (CONTEXT if setting == "readout" else ())
        table = []
        for device, label in ORDER:
            if not any(r["key"] == _key(device, label) and r["setting"] == setting for r in rows):
                continue
            table.append(
                [_case_name(device, label)]
                + [
                    _short(
                        _row(
                            rows,
                            key=_key(device, label),
                            setting=setting,
                            method=m,
                            reference="fixed",
                        )
                    )
                    for m in cols
                ]
            )
        w(_table(["Case", *(SHORT[m] for m in cols)], table))
        w("")

    w("## 4. Cross-fitted regret")
    w("")
    w(
        "Relative regret against the cross-fitted best placement (P1-2): for each base seed, the "
        "best placement on the other two seeds, measured on the held-out seed. Zero means the "
        "method found the best placement; the verdict is the interval's."
    )
    w("")
    fits = result["cross_fit"]
    shown = ("decoder-graph", "v1", "lowest-summed-error", "qiskit-default-l1", "mapomatic")
    table = []
    for device, label in ORDER:
        key = _key(device, label)
        if f"readout:{key}" not in fits:
            continue
        cells = []
        for m in shown:
            for setting in ("zero", "readout"):
                reg = fits.get(f"{setting}:{key}", {}).get("regret", {}).get(m)
                cells.append(
                    "–"
                    if reg is None
                    else _minus(f"{100 * reg['relative']['estimate']:+.1f}% {reg['verdict']}")
                )
        table.append([_case_name(device, label), *cells])
    w(
        _table(
            ["Case"]
            + [
                f"{SHORT[m]}, {'0' if s == 'zero' else 'readout'}"
                for m in shown
                for s in ("zero", "readout")
            ],
            table,
        )
    )
    w("")

    w("## 5. Which choices change between the settings?")
    w("")
    table = []
    for m, c in result["choice_changes"].items():
        names = ", ".join(_case_name(*k.split("-", 1)) for k in c["changed"]) or "none"
        table.append([NAMES[m], f"{len(c['changed'])} of {c['cases']}", names])
    w(_table(["Method", "Changed", "Cases"], table))
    w("")

    w("## Reproducibility")
    w("")
    m = prov["machine"]
    w(
        "```bash\npython -m nac_bench.run_reset configs_v3/reset_sensitivity.json\n"
        "python -m nac_bench.reset_analysis configs_v3/reset_sensitivity.json\n```"
    )
    w("")
    w(
        f"Runs: {prov['runs']['cases']} cases, {prov['runs']['wall_clock_seconds'] / 60:.1f} minutes "
        f"wall clock on {', '.join(prov['runs']['machine'])} with "
        f"{', '.join(map(str, prov['runs']['workers']))} workers, code commit "
        f"`{', '.join(c[:7] for c in prov['runs']['commits'])}`"
        f"{' (uncommitted changes)' if prov['runs']['dirty'] else ''}. "
        f"Analysis machine: {m['cpu_model']}, Python {m['python']}, {m['stim_build']}. Versions: "
        + ", ".join(f"{k} {v}" for k, v in prov["versions"].items())
        + ". Config SHA-256 `"
        + prov["config_sha256"][:16]
        + "…`."
    )
    w("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config", type=Path)
    parser.add_argument("--runs", type=Path, default=ROOT / "results_v3" / "reset" / "runs")
    parser.add_argument("--out", type=Path, default=ROOT / "results_v3" / "reset")
    parser.add_argument("--machine-label", default=None)
    args = parser.parse_args(argv)

    config = json.loads(args.config.read_text(encoding="utf-8"))
    config["_config_sha256"] = hashlib.sha256(args.config.read_bytes()).hexdigest()
    result, entries = analyse(config, args.runs)
    raws = [e["case"].raw for e in entries if e["source"] == "v3 reset run"]
    commit, dirty = git_state()
    result["provenance"] = {
        "package_git_commit": commit,
        "package_git_dirty": dirty,
        "config_sha256": config["_config_sha256"],
        "written_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "machine": machine(args.machine_label, workers=1),
        "versions": {
            name: importlib.metadata.version(name)
            for name in ("qiskit", "mapomatic", "numpy", "stim", "pymatching")
        },
        "runs": {
            "cases": len(raws),
            "wall_clock_seconds": sum(r["wall_clock_seconds"] for r in raws),
            "workers": sorted({r["workers"] for r in raws}),
            "machine": sorted({r["provenance"]["machine"]["cpu_model"] for r in raws}),
            "commits": sorted({r["provenance"]["package_git_commit"] for r in raws}),
            "dirty": any(r["provenance"]["package_git_dirty"] for r in raws),
        },
    }
    v1 = json.loads(
        (ROOT / "results_v3" / "reanalysis" / "reanalysis.json").read_text(encoding="utf-8")
    )
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "reset.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    path = write_report(result, v1["comparisons"], args.out / "RESET.md")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
