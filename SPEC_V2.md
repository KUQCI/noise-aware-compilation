# Noise-Aware Placement for Nove: v2 Spec

Oct 11, 2026 · @Khaled Al Daqrouq

**Revision 1 (Oct 11, 2026). Made before any v2 result existed.** Approved by Khaled in the project thread after the pre-implementation review (project file `v2-spec-review/REVIEW.md`). No v2 benchmark had been run and no FakeAlgiers result existed. The changes are listed here with their reasons (R2-R6). Everything else is unchanged.

1. **Placement counts corrected.** FakeHanoiV2 is 80 / 102, 74 (the v1 rev 2 numbers; 100 / 164, 178 were from before rev 2 removed two couplers). For FakeAlgiers, the unchanged v1 converter removes coupler (15,18), giving 84 / 102, 86. K11 reworded to match.
2. **Drift granularity (R-S2, T2-S2).** One factor per noise channel, so a gate's error drifts by δ rather than about δ/√15. The capping rule is stated. Zero-valued parameters are excluded from T2-S2, which may use extra draws.
3. **Qiskit baseline pinned (R-B5, T2-B3).** An op-for-op Qiskit circuit, a Target holding each channel's total error probability, and fixed VF2Layout settings. Without these, the choice depended on which operations were included and on an unseeded shuffle.
4. **M6 interval and gate.** The gate is per method. The interval covers both draw spread and shot noise.
5. **M7 harm** means a pooled verdict of "worse" against fixed.
6. **R-G7 metric and interval.** Relative regret, with a shot-noise interval. The verdict covers only the four held-out cases.
7. **Freeze order (R-G8, new).** Develop only on v1's existing data, freeze, then convert FakeAlgiers and produce every final v2 result. Part A and B results on the development devices are labelled development-device results (R2-R7, new), and only FakeAlgiers is an independent test.
8. **R-B9.** Both scores are compared with every baseline.
9. **Grading decoder (Part A, K9).** The decoder uses true-model weights, and this is stated explicitly.

## Goal and definition of done

v2 extends the v1 package to answer three questions, and makes it usable by others:

1. Does the noise-aware placement still help when it chooses with stale calibration data? (Part A)
2. Does it beat placement methods people already use? (Part B)
3. Does a score built from the decoder's error graph choose better than the v1 score, on a device it was never developed on? (Part C)
4. Can anyone place their own circuit on their own calibration data with one command? (Part D)

v2 is done when every test in the test specification passes, every reporting requirement is met, and every v1 test still passes. No question has a required answer: if the method loses its gain under drift, or loses to an existing layout, a correctly measured result is an acceptable outcome.

## Settled decisions

The v1 spec stays in force except where this table or a later section changes it. IDs from v1 (requirements R-, constraints K1–K8, prohibitions X1–X10) keep their meaning.

| Topic | Decision |
| --- | --- |
| Code location | The same package and repo as v1 (`KUQCI/noise-aware-compilation`); v2 extends it |
| Routing, evaluation, decoder | Unchanged from v1: SWAP-free only, classical simulation only, PyMatching with per-placement weights |
| Development devices | FakeJakartaV2 and FakeHanoiV2, unchanged from v1 |
| Held-out device | FakeAlgiers (27 qubits, snapshot dated 2024-02-28, same raw coupling map as FakeHanoiV2), used only for the final evaluation of Part C (rev 1: layout wording) |
| Circuits | v1 circuits A, B and D; circuit C is not used in v2 |
| Drift model | Random jitter of every noise channel, at several strengths (rev 1: per channel) |
| New baselines | Lowest summed error, and Qiskit's noise-aware layout |
| Qiskit | Allowed only as a benchmark-only optional dependency for the Qiskit baseline; placement, evaluation and the CLI run without it |
| New score | A decoder-graph score added beside the v1 score; the v1 score stays available |
| Interface | A command-line tool and a documented Python API |
| Seeds | All v2 evaluation seeds are new and disjoint from every v1 seed |
| Claims | A gain is claimed only when the verdict from all seeds pooled is "better" |

## Part A: robustness to stale calibration

Every noise-aware method chooses with a drifted ("seen") noise model and is graded under the original ("true") noise model. The true model is the v1 converted snapshot. Grading reuses the true-model measurements of every placement, so each placement is decoded with weights from the true model; Part A isolates the effect of stale calibration on placement only (rev 1).

| ID | Requirement |
| --- | --- |
| R-S1 | Selection and evaluation take separate noise models; every results file records both models' hashes |
| R-S2 | A seen model at drift strength δ multiplies every noise channel by an independent factor exp(δ·z), z drawn from a standard normal distribution. A channel is each directed CX's Pauli channel, each qubit's H channel, each qubit's idle rates (X, Y and Z scaled together), each qubit's readout flip and each qubit's reset flip; all probabilities of a channel get the same factor. Caps keep every channel valid: a Pauli channel whose total would exceed 1 is scaled down proportionally to total 1, and a flip probability is capped at 0.5 (rev 1) |
| R-S3 | Drift strengths: δ ∈ {0, 0.1, 0.25, 0.5} |
| R-S4 | At least 20 independent seen models per drift strength, each from its own recorded seed |
| R-S5 | Every noise-aware method (the v1 score, the decoder-graph score, lowest summed error, Qiskit's layout) receives the identical seen model for a given draw |
| R-S6 | At δ = 0, every method's selection equals its selection under the true model |
| R-S7 | Noise-blind baselines (fixed, all-placement average) are evaluated under the true model only |

**Metrics**

| ID | Metric | Definition |
| --- | --- | --- |
| M6 | Retained gain | Per method: (P\_L fixed − P\_L of the placement that method chose from the seen model) ÷ (P\_L fixed − P\_L of the placement that method chose from the true model), averaged over draws. The 95% interval is a percentile bootstrap that resamples draws and redraws every placement's failure counts from its binomial, so it covers draw spread and shot noise. M6 can exceed 1 or be negative (rev 1) |
| M7 | Harm rate | Fraction of draws in which the placement chosen from the seen model is *worse* than the fixed baseline by the pooled verdict under the true model, with a Wilson 95% interval over draws (rev 1) |

M6 is reported for a method only where that method's δ = 0 choice is "better" than fixed by the pooled verdict; elsewhere the report states that there is no gain to retain.

## Part B: stronger baselines

Two noise-aware baselines join v1's fixed and all-placement-average baselines. Each is held to the same rules as the package's own method.

| ID | Baseline | Definition |
| --- | --- | --- |
| R-B4 | Lowest summed error | The valid placement whose placed circuit has the lowest sum of total error probability over every gate, measurement and reset operation (idle noise excluded); ties broken in lexicographic order of the mapping |
| R-B5 | Qiskit layout | The placement chosen by Qiskit's `VF2Layout` pass on a Qiskit Target built from the same noise data the package's method receives. The Qiskit circuit is an op-for-op translation of the circuit (R→reset, H→h, CX→cx with the same directions, M→measure). Each Target instruction's error is the total error probability of its Nove channel: the sum of the Pauli probabilities for cx (per direction) and h, the readout flip for measure, and the reset flip for reset. T1 and T2 are given as qubit properties. The pass settings are `seed=-1, strict_direction=True, call_limit=None, max_trials=None, time_limit=None`. The Qiskit version is pinned and recorded (rev 1) |

| ID | Requirement |
| --- | --- |
| R-B6 | Both baselines draw only from the same candidate set as the package's method: SWAP-free placements satisfying R-P1 |
| R-B7 | A case where the Qiskit baseline returns no placement, or one outside the candidate set, is recorded as a failure of that baseline for that case; no substitute placement is used |
| R-B8 | Both baselines receive the same seen model as every other noise-aware method in Part A (R-S5) |
| R-B9 | Each of the package's two scores' selected placements is compared with every baseline (fixed, all-placement average, lowest summed error, Qiskit layout) using v1's comparison rules; the default score is marked (rev 1) |

## Part C: decoder-graph score and held-out evaluation

A second score estimates each placement's logical failure probability from its decoder's error graph. It may be developed only on FakeJakartaV2 and FakeHanoiV2, and is judged on FakeAlgiers, a device it has never seen.

| ID | Requirement |
| --- | --- |
| R-G1 | The decoder-graph score of a placement is computed from that placed circuit's detector error model, and accounts for which error mechanisms flip a logical observable |
| R-G2 | The score uses no sampling or simulation of logical error rates (v1 R-P4 applies) |
| R-G3 | The v1 score stays selectable; the default score is set only by R-G7 |
| R-G4 | The decoder-graph score's code is frozen in a tagged commit before any FakeAlgiers result is produced, and is not changed afterwards |
| R-G5 | FakeAlgiers is converted by the unchanged v1 converter and stored with its SHA-256 hash like the other devices |
| R-G6 | Held-out evaluation on FakeAlgiers covers circuits A, B and D under its real noise, with fresh seeds, for both scores and every baseline |
| R-G7 | The decoder-graph score becomes the default only if its mean relative regret across the four held-out cases (A, B, D5, D7) is lower than the v1 score's, with the 95% interval of the difference excluding zero; otherwise the v1 score stays the default. A case's relative regret is (P\_L of the chosen placement − oracle-best P\_L) ÷ oracle-best P\_L, all pooled over seeds. The interval comes from shot noise only (the cases are fixed): a delta-method normal interval over the independent binomial estimates of the placements involved. If both scores choose the same placement in every case, the difference is exactly 0 and the v1 score stays the default. The verdict covers only these four cases (rev 1) |
| R-G8 | The decoder-graph score is developed only against v1's existing measurements on the development devices. After the freeze (R-G4), FakeAlgiers is converted and every final v2 result (Parts A, B and C) is produced with fresh seeds by the frozen code (rev 1) |

The decision rule in R-G7 is fixed before any held-out result exists, so that it cannot be chosen to fit the outcome.

## Part D: user interface

Anyone can convert their device data and place their own circuit, from the command line or from Python, without running any benchmark.

| ID | Requirement |
| --- | --- |
| R-I1 | A `convert` command turns an IBM fake-backend file pair (`conf_*.json`, `props_*.json`) into a stored converted device (Nove `CalibrationSnapshot` and `DeviceModel` JSON, with SHA-256 hashes) |
| R-I2 | A `place` command takes a circuit as a Stim text file, a device as either an IBM file pair or a stored converted device, a scorer choice (v1 or decoder-graph), and an output directory |
| R-I3 | `place` writes a mapping file (JSON: chosen mapping, its score, the scorer, every candidate with its score, input file hashes, package version) and the relabelled circuit as a Stim text file |
| R-I4 | The Python API offers the same two operations with the same inputs and outputs, documented in docstrings |
| R-I5 | The command line and the API give identical results for identical inputs |
| R-I6 | Neither command requires Qiskit, a decoder or any simulation |
| R-I7 | Invalid input (unreadable circuit, missing or incomplete calibration fields, no valid placement) gives a non-zero exit code and a one-line message naming the problem, with no traceback unless a debug flag is set |
| R-I8 | `--help` documents every command and option |
| R-I9 | The README has a worked example, from a fresh install to a placed circuit, using a circuit and device shipped with the package |

## Benchmark specification

Every valid placement's true P\_L is measured once per seed, on 3 fresh seeds with at least 200,000 shots each (more where v1's T-P4 is not met). Every method's choice, in every drift draw, is graded with those same measurements, so drift adds selections but no extra simulation.

| Device | Role | Circuits | Valid placements: A and B / D (distance 5, 7) | Noise |
| --- | --- | --- | --- | --- |
| FakeJakartaV2 | Development | A, B | 8 / does not fit | True model, plus seen models for Part A |
| FakeHanoiV2 | Development | A, B, D | 80 / 102, 74 (rev 1) | True model, plus seen models for Part A |
| FakeAlgiers | Held-out (Part C only) | A, B, D | 84 / 102, 86 (rev 1; coupler (15,18) removed by the converter) | True model only |

| Circuit | Definition |
| --- | --- |
| A | Distance-3 bit-flip repetition code, memory experiment, 3 rounds |
| B | Distance-3 phase-flip repetition code, 3 rounds |
| D | Distance-5 and distance-7 bit-flip repetition codes, 3 rounds |

| Method | Uses noise data | Part A (drift) | Part C (held-out) |
| --- | --- | --- | --- |
| v1 score | Yes | Yes | Yes |
| Decoder-graph score | Yes | Yes | Yes |
| Lowest summed error | Yes | Yes | Yes |
| Qiskit layout | Yes | Yes | Yes |
| Fixed | No | True model only | Yes |
| All-placement average | No | True model only | Yes |
| Oracle best and worst | Evaluation only | True model only | Yes |

## Test specification

Every test has a mechanically checkable pass criterion. None requires a method to win; performance tests pass when a comparison is computed and reported correctly, whatever the verdict. Wherever verdict consistency is checked, v1's amended rule applies: a test fails only when two verdicts for the same comparison are opposite ("better" and "worse").

| ID | Area | Test | Pass criterion |
| --- | --- | --- | --- |
| T2-V1 | Regression | v1 suite | Every v1 test still passes |
| T2-S1 | Drift | Zero drift | At δ = 0, every noise-aware method selects exactly what it selects under the true model (R-S6) |
| T2-S2 | Drift | Drift distribution | Over ≥ 10⁴ drifted parameters at each δ > 0, the standard deviation of log(seen ÷ true) is within 5% of δ (before capping, excluding parameters whose true value is 0, using as many draws as needed), and every channel is valid (rev 1) |
| T2-S3 | Drift | Seeded draws | The same draw seed gives a bit-identical seen model |
| T2-S4 | Drift | Shared seen model | Within each draw, the seen-model hash recorded for every noise-aware method is identical (R-S5) |
| T2-S5 | Drift | Grading uses true model | Every graded result records a true-model hash equal to the v1 snapshot's, and a seen-model hash that differs from it whenever δ > 0 |
| T2-S6 | Drift | Draw count | At least 20 draws per drift strength per case (R-S4) |
| T2-B1 | Baselines | Summed-error values | On FakeJakartaV2, the summed-error score of 2 placements matches a hand calculation exactly |
| T2-B2 | Baselines | Qiskit placements valid | Every Qiskit-baseline placement is in the candidate set, or is recorded as a failure (R-B7) |
| T2-B3 | Baselines | Qiskit sees the same data | For one placement, the Qiskit Target's error rates equal the total error probabilities of the package's noise channels (R-B5) for every qubit and connection it uses (rev 1) |
| T2-B4 | Baselines | Qiskit stays optional | The package's tests other than the Qiskit-baseline tests, and both CLI commands, pass with Qiskit not installed |
| T2-G1 | Score | No sampling | Selection with the decoder-graph score completes with every sampler disabled (mocked to raise) |
| T2-G2 | Score | Freeze before held-out | The tagged commit freezing the decoder-graph score predates the first FakeAlgiers result file, and the score's code has no later changes |
| T2-G3 | Score | Held-out device untouched | No results file for FakeAlgiers exists before the freeze commit, and its converted device hash matches the stored file |
| T2-G4 | Score | Default rule applied | The default score matches the outcome of R-G7 computed from the held-out results |
| T2-I1 | Interface | End-to-end CLI | `convert` then `place` on the shipped example exit with code 0 and write a valid mapping and a relabelled circuit that Nove accepts |
| T2-I2 | Interface | Input formats agree | `place` gives the same mapping from an IBM file pair as from its stored converted device |
| T2-I3 | Interface | CLI and API agree | For every shipped example, the CLI and API mapping files are identical apart from timestamps |
| T2-I4 | Interface | Error handling | Each invalid input in R-I7 gives a non-zero exit code and a one-line message naming the problem, with no traceback |
| T2-I5 | Interface | README example | The README example runs as written in a clean environment |
| T2-I6 | Interface | Help text | `--help` lists every command and option the code accepts |
| T2-R1 | Reproducibility | Same seeds | Re-running any case with the same seeds gives bit-identical failure counts and selections |
| T2-R2 | Reproducibility | Provenance | Every results file records the v1 provenance fields plus seen- and true-model hashes, drift strength, draw seed, scorer, and Qiskit version where used |
| T2-P1 | Performance | Retained gain | M6 is reported with 95% intervals for every noise-aware method, case and δ > 0, or the report states that there is no gain to retain |
| T2-P2 | Performance | Harm rate | M7 is reported with 95% intervals for every noise-aware method, case and δ > 0 |
| T2-P3 | Performance | Baseline comparison | The package's choice vs every baseline is reported per case with a pooled verdict and the per-seed verdicts, and no two verdicts for one comparison are opposite |
| T2-P4 | Performance | Held-out comparison | On FakeAlgiers, both scores' M4, M5 and relative regret are reported per case, along with the R-G7 calculation |

## Reporting requirements

The v2 report keeps v1's rules (R-R1–R-R8, results stated "under this noise model") and adds the following.

| ID | Requirement |
| --- | --- |
| R2-R1 | For each device and circuit, a plot of retained gain (M6) and harm rate (M7) against drift strength, one line per noise-aware method, with interval bars |
| R2-R2 | A comparison table of the package's choice vs every baseline per case: pooled verdict, per-seed verdicts in the form "better (2/3 seeds)", and the absolute and relative differences with 95% intervals |
| R2-R3 | A gain is called a gain only when the pooled verdict is "better"; cases where seeds disagree without contradicting are labelled "weak evidence" |
| R2-R4 | A held-out section for FakeAlgiers: both scores' M4, M5 and relative regret per case, the R-G7 calculation, the resulting default score, and the statement that the verdict covers only these four cases (rev 1) |
| R2-R5 | A plain-language answer to each of the goal's first three questions, citing the table or plot that supports it |
| R2-R6 | A record of every change made to this spec after results were seen, with its date and reason |
| R2-R7 | Part A and Part B results on FakeJakartaV2 and FakeHanoiV2 are labelled development-device results: the decoder-graph score was developed on those devices, so fresh seeds do not make them an independent test. Only FakeAlgiers is treated as the real test (rev 1) |

## Constraints, scope and prohibited actions

**Constraints** (v1's K1–K8 still apply; the report must also disclose these)

| ID | Constraint |
| --- | --- |
| K9 | Random jitter is synthetic. Real drift is correlated across qubits and over time, so Part A measures sensitivity to calibration error, not any real device's drift. Choices are decoded with true-model weights, whereas a real decoder would also use the stale calibration (rev 1) |
| K10 | The Qiskit baseline's behaviour depends on the Qiskit version; its results apply to the pinned version only |
| K11 | FakeAlgiers has the same raw coupling map as FakeHanoiV2, so it is held out mainly for noise values, not for device layout; after conversion the layouts differ only in which dead couplers were removed ((15,18) on FakeAlgiers; (5,8) and (19,20) on FakeHanoiV2) (rev 1) |

**Out of scope for v2**

- Larger devices, and devices whose two-qubit gate is ECR or CZ
- Codes other than repetition codes
- Routing and SWAP insertion
- Hardware runs
- Correlated or time-series drift models
- Integration as a Nove compiler pass; Nove has no pass mechanism
- Qiskit routing or full transpilation beyond the layout pass
- Changes to Nove

**Prohibited actions** (v1's X1–X10 still apply)

| ID | Prohibited action |
| --- | --- |
| X11 | Change the drift strengths, draw counts or drift model after seeing any Part A result |
| X12 | Produce or inspect any FakeAlgiers result before the freeze commit (R-G4), or change the decoder-graph score after it |
| X13 | Give any baseline different noise data, candidates or circuit interactions than the package's method |
| X14 | Modify, rerun or delete v1 results |
| X15 | Make Qiskit a dependency of the core package or the CLI |

## Sources and provenance

Pinned versions: Nove commit `485b413`, `qiskit-ibm-runtime` 0.50.0 (FakeJakartaV2 props dated 2024-05-27; FakeHanoiV2 2025-02-26; FakeAlgiers 2024-02-28). The Qiskit version for the layout baseline is pinned in the benchmark config (`qiskit==2.5.2`, the version `qiskit-ibm-runtime` 0.50.0 resolves to; rev 1) and recorded in every results file.

- [Noise-Aware Placement for Nove: v1 Spec](https://claude.ai/code/artifact/5ec7af84-2f93-4621-a117-5997f036f06f): the requirements, constraints and prohibitions this spec extends
- [KUQCI/noise-aware-compilation](https://github.com/KUQCI/noise-aware-compilation): the v1 package
- [KUQCI/nove repository](https://github.com/KUQCI/nove)
- [qiskit-ibm-runtime Fake Provider](https://quantum.cloud.ibm.com/docs/en/api/qiskit-ibm-runtime/fake-provider): FakeJakartaV2, FakeHanoiV2 and FakeAlgiers
