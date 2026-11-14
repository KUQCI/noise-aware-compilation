# Benchmarks

Supplementary material: the code, configurations and raw results behind
[docs/results.md](../docs/results.md). The package in `src/` does not depend on anything here.

## Layout

| Folder | Contents |
| --- | --- |
| `data/devices/` | IBM's raw calibration files (`conf_*.json`, `props_*.json`), including dated snapshots for drift |
| `configs/`, `configs_v2/`, `configs_v3/` | Run configurations with every seed, one folder per version |
| `nac_bench/` | Benchmark code for v2 and v3 (runners, baselines, statistics, analysis) |
| `scripts/` | Helper scripts: device snapshot builder, report builder, environment checks |
| `results/`, `results_v2/`, `results_v3/` | Raw run files (failure counts, seeds, provenance), summaries and figures |
| `tests/` | Checks that the committed results and reports agree with the code |
| `requirements-lock.txt` | Exact package versions the runs used |

## Reproducing

Install with the benchmark extras and run from this folder:

```bash
python -m pip install -e "..[dev,bench]"
cd benchmarks
```

| Result | Command |
| --- | --- |
| v1 | `python -m noise_aware_compilation.benchmark configs/benchmark.json`, then `python -m noise_aware_compilation.report --out ../docs/reports/REPORT.md` |
| v2 | `python -m nac_bench.run_v2 configs_v2/benchmark_v2.json`, then `python -m nac_bench.report_v2` |
| v3 re-analysis of v1 and v2 | `python -m nac_bench.reanalysis configs_v3/reanalysis.json` |
| v3 reset study | `python -m nac_bench.run_reset configs_v3/reset_sensitivity.json` |
| v3 drift | `python -m nac_bench.run_drift configs_v3/drift.json`, then the same with `--analyse` |
| v3 held-out test | `python -m nac_bench.run_v3 configs_v3/held_out.json`, then `python -m nac_bench.analysis_v3 configs_v3/held_out.json` and `python scripts/report_v3.py` |

Every runner takes `--only CASE_ID ...` to run part of a config and `--workers N`. Every
Monte Carlo seed is in the configs. The held-out test is the expensive one: about 61 wall
hours on 48 cores (2,700 core-hours).

The v3 analysis also reads per-placement score files (2.3 GB) that are not committed; the
run files record their hashes.

## The frozen record

The v3 test was pre-registered: the score, code and configuration were frozen (as git tags)
before the held-out devices were converted. This folder was reorganised afterwards, so the
freeze checks, which verify every frozen file byte for byte, run at tag `v3.0-results`. See
[docs/methodology/](../docs/methodology/README.md).
