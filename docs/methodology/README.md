# Methodology

How each version of the study was planned, frozen and reviewed. These files are the research
record. They are kept exactly as they were written, because the freeze records hash them, so
they still use the file paths of the original repository layout (see below).

You do not need any of them to use the package. Read them to check how a result was obtained,
or why a rule is the way it is.

| File | What it is |
| --- | --- |
| [SPEC.md](SPEC.md) | v1 plan: devices, circuits, the v1 score, the statistics, and what counts as a win. Written before any v1 result. |
| [DECISIONS.md](DECISIONS.md) | v1 decision log: every choice the spec left open, with its reason and date. |
| [SPEC_V2.md](SPEC_V2.md) | v2 plan: the decoder-graph score, Qiskit as a baseline, synthetic drift, the held-out FakeAlgiers. |
| [DECISIONS_V2.md](DECISIONS_V2.md) | v2 decision log. |
| [EVALUATION.md](EVALUATION.md) | Review of v1 and v2 that set the v3 plan (held-out devices, reset error, stronger baselines). |
| [SPEC_V3.md](SPEC_V3.md) | v3 pre-registration (revision 6): the frozen score, seeds, held-out devices and verdict rules. |
| [DECISIONS_V3.md](DECISIONS_V3.md) | v3 decision log, including each spec revision and why. |
| [REVIEW_PRE_F4.md](REVIEW_PRE_F4.md) | Review held before the held-out calibration files were opened, with its blocking items. |

## Freezes

Each freeze is a git tag. A freeze commits the score, the code and the run configuration
before the data it will be judged on is produced.

| Tag | What it froze |
| --- | --- |
| `v3-score-freeze` | Both scores and the fast search (F3, Nov 9) |
| `v3-score-freeze-surface-k` | The surface-code scaled score at k = 640 (F3, second commit) |
| `v3-held-out-config` | The held-out run configuration (F4, revision 5), before any held-out file was converted |
| `v3-held-out-config-r6` | The held-out configuration and code freeze for revision 6, the one the runs used |
| `v3.0-results` | The finished v3 results, in the original repository layout |

The freeze checks (tests that verify every frozen file is unchanged) run at `v3.0-results`:

```bash
git checkout v3.0-results
python -m pip install -e ".[dev,bench]"
pytest
```

## Paths in these files

The repository was reorganised after `v3.0-results`. A path in these files maps to the
current layout as follows:

| In these files | Now |
| --- | --- |
| `REPORT*.md` | `docs/reports/` |
| `SPEC*.md`, `DECISIONS*.md`, `EVALUATION.md`, `REVIEW_PRE_F4.md` | `docs/methodology/` |
| `nac_bench/`, `scripts/`, `configs*/`, `data/`, `results*/` | `benchmarks/` |
| `src/`, `tests/` | unchanged |
