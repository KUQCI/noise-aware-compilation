# Noise-Aware Placement for Nove: v1 Plan

Oct 11, 2026 · @Khaled Al Daqrouq

**Revision 2 (Oct 11, 2026).** Approved by Khaled in the project thread after the v1 audit. The conversion rule now removes any coupler whose CX `gate_error` exceeds 0.8 in either direction (the largest value the CX rule can turn into a Pauli channel), instead of v1's cap at 1/15 per term. This changes the FakeHanoiV2 placement counts (benchmark table and T-C3) and adds one T-L1 variant. Nothing else changed.

**Revision 3 (Oct 11, 2026). Pass criteria changed after seeing results.** Requested by Khaled in the project thread, with the project lead asking for the looser reading of T-P5. T-P5 previously required "the verdict's direction is the same for all three" round counts, and T-R2 required "the verdict is the same across all 3 seeds"; both were applied strictly (identical verdicts). Both now require only that no two verdicts for the same comparison point in opposite directions (*better* together with *worse*); *indistinguishable* next to either is consistent.

- **Why:** a strict match turns a loss of statistical power into a failure. An *indistinguishable* verdict means the 95% intervals overlap, not that the effect changed sign, so it is not evidence against a *better* verdict elsewhere. The spec's own wording for T-P5 ("direction") supports this reading.
- **Disclosure:** this change was made **after** the rev 2 results were known. Under the old wording, rev 2 failed T-R2 (FakeHanoiV2 d=7 N2: *better*, *indistinguishable*, *better* vs fixed across seeds) and T-P5 (FakeHanoiV2 circuit A vs fixed: *indistinguishable*, *indistinguishable*, *better* at 1, 3, 5 rounds). Under the new wording both pass. No result, seed, shot count or case was changed or rerun. The report shows both readings side by side.
- **What the new criteria no longer check:** that a gain is large enough to be resolved on every seed or every round count. A gain that is resolved on one seed only now passes, as long as no seed points the other way.

**Revision 4 (Oct 11, 2026). Analysis and reporting rule added after seeing results.** Requested by Khaled in the project thread. Three changes, all to how verdicts are reported and claimed (R-R2, T-P1, X8):

1. **Pooled verdict.** Each comparison's verdict is the one computed from all three seeds' shots combined. This is a recalculation from the saved counts, not a rerun (X7 is unaffected). The analysis code already computed the headline verdicts this way in v1 and rev 2; rev 4 makes it the stated rule and names it in the report.
2. **Seed breakdown.** Next to each pooled verdict the report shows how many seeds give it on their own shots, e.g. "better (2/3 seeds), pooled: better".
3. **What may be claimed (X8 made specific).** A gain is claimed only when the pooled verdict is *better*. When not every seed shows *better* on its own, the report labels it **weak evidence**. A pooled *worse* is reported the same way as a loss. A pooled *indistinguishable* claims nothing; if some seed alone resolves a difference, it is labelled *seeds mixed*.

- **Why:** pooling gives one estimate with a tighter interval, which is the direct answer to "is there a gain?"; the per-seed tally shows how fragile that answer is.
- **Disclosure:** added **after** the rev 2 results were known. No result, seed, shot count or case was changed or rerun. On the rev 2 N2 results it labels one headline as weak evidence (FakeHanoiV2 d=7 vs fixed: better on 2 of 3 seeds, pooled better, −19%) and changes no verdict.

## Goal and definition of done

A separate Python package, built on Nove, that chooses a SWAP-free placement of a QEC circuit's qubits on a device using that device's calibrated noise. The package must also measure, in simulation, whether that choice lowers the decoded logical error rate compared with a fixed placement and with every other valid placement.

The work is done when every test in the test specification passes and every reporting requirement is met. A measurable improvement is **not** a requirement: a correctly measured "no gain" or "worse" result is an acceptable outcome.

## Settled decisions

All decisions below are final for v1.

| Topic | Decision |
| --- | --- |
| Code location | A separate package that depends on Nove; Nove itself is not modified |
| Routing | SWAP-free placements only |
| Evaluation | Classical simulation only; no hardware runs |
| Devices | FakeJakartaV2 (7 qubits) and FakeHanoiV2 (27 qubits) from `qiskit-ibm-runtime` 0.50.0, treated as realistic noise profiles, not live-device measurements |
| Circuits | Repetition codes only |
| Primary metric | Decoded logical error rate, per shot and per round |
| Secondary metric | Detection-event rate |
| Decoder | PyMatching with per-placement weights from each placement's own noise model |
| Fixed baseline | The first valid placement in lexicographic order, chosen without noise information |
| Selection | A score computed from the device and noise data; selection must not sample candidates' logical error rates |
| Noise conversion | As specified under Noise conversion specification |
| Nove `to_detector_model()` bug | Reported upstream by the project owner; outside this work |

## Deliverables and interface requirements

The package delivers the components below. It builds on Nove commit `485b413` (version 0.1.0) and uses Nove's own types for devices, noise and circuits.

| ID | Requirement |
| --- | --- |
| R-D1 | A placement entry point that takes a circuit, a Nove `DeviceModel` and a Nove `HardwareNoiseModel`, and returns the chosen mapping (circuit qubit → physical qubit), its score, and every valid candidate mapping with its score |
| R-D2 | For any valid mapping, the relabelled circuit, which Nove accepts with that device and noise model |
| R-D3 | A converter from `qiskit-ibm-runtime` fake-backend files (`conf_*.json`, `props_*.json`) to a Nove `CalibrationSnapshot` and `DeviceModel`, following the Noise conversion specification |
| R-D4 | Converted snapshots for FakeJakartaV2 and FakeHanoiV2 stored in the package with their SHA-256 hashes |
| R-D5 | Placement and evaluation accept any `DeviceModel` and `HardwareNoiseModel`; nothing device-specific exists outside the converter |
| R-D6 | Qiskit is not a runtime dependency; device data is read from the stored files |
| R-D7 | A benchmark runner that reproduces every reported number from a config file and seeds |
| R-D8 | A machine-readable results file per run, with the provenance fields in T-R3 |
| R-D9 | A results report meeting the Reporting requirements |
| R-D10 | The package's own test suite, linter and type checker pass |

## Placement, baseline and fairness requirements

**Placement**

| ID | Requirement |
| --- | --- |
| R-P1 | Every returned placement is one-to-one and maps every two-qubit gate pair onto a device connection, with no SWAPs added |
| R-P2 | The candidate set contains every valid SWAP-free placement |
| R-P3 | When no valid placement exists, a clear error is raised and nothing else is returned |
| R-P4 | Selection uses only the circuit, device and noise data; it does not sample or simulate any candidate's logical error rate |
| R-P5 | Identical inputs give an identical placement; ties are broken by a documented deterministic rule |
| R-P6 | Relabelling preserves the circuit's detectors, observables and measurement order |

**Baselines**

| ID | Baseline | Definition |
| --- | --- | --- |
| R-B1 | Fixed | The first valid placement in lexicographic order of the mapping; uses no noise information |
| R-B2 | All-placement average | Mean P\_L over every valid placement, equal to the expected value of a random valid placement |
| R-B3 | Oracle best and worst | The lowest and highest P\_L found by simulating every valid placement; used only to measure headroom and regret, never for selection |

**Fairness**

| ID | Requirement |
| --- | --- |
| R-F1 | Every placement, chosen or baseline, is evaluated with the same circuit, noise model, shot count and decoder-construction rule |
| R-F2 | Circuits are generated noiseless; all noise comes from the Nove noise model |
| R-F3 | Evaluation seeds are disjoint from any seeds used during selection |
| R-F4 | The fixed baseline and the benchmark config are committed before the first run under real noise (N2) |
| R-F5 | Placements are treated as equivalent (e.g. mirror images) only after T-C7 shows them equivalent |

## Metrics and decoder specification

| ID | Metric | Definition |
| --- | --- | --- |
| M1 | Logical error per shot, P\_L | Fraction of shots in which any observable is predicted wrongly, with a Wilson 95% interval |
| M2 | Logical error per round, ε | Computed from P\_L over r rounds by the formula below |
| M3 | Detection-event rate | Fraction of detectors that fire, per placement |
| M4 | Score validity | Spearman rank correlation between each placement's selection score and its simulated P\_L |
| M5 | Rank and regret | The chosen placement's rank among all valid placements, and its P\_L gap to the oracle best |

```latex
\varepsilon = \tfrac{1}{2}\left[1 - (1 - 2P_L)^{1/r}\right]
```

The formula assumes rounds fail independently and P\_L < 0.5.

**Decoder.** PyMatching, built separately for each placement from that placement's own detector error model. For calibrated two-qubit noise this model is Stim's approximate one (see Constraints), and every results file records that the decoder model is approximate.

Distance-2 codes detect errors but cannot correct them, so their results report M3 only.

## Noise conversion specification

The converter maps each fake-backend field to a Nove value by the rules below, applied the same way to both devices. Times are in nanoseconds.

| Nove quantity | Source field | Rule |
| --- | --- | --- |
| Connectivity | `coupling_map` in `conf_*.json` | Every listed pair, except a pair whose `cx` `gate_error` exceeds 0.8 in either direction, which is removed in both directions (rev 2) |
| CX Pauli channel, each direction | `cx` `gate_error` e for that ordered pair | 15 two-qubit Pauli probabilities, each 1.25 · e / 15; defined for e ≤ 0.8 |
| H Pauli channel, per qubit | `sx` `gate_error` e for that qubit | p\_x = p\_y = p\_z = 1.5 · e / 3 |
| Idle Pauli rates, per qubit | `T1`, `T2` | X rate = Y rate = 1/(4·T1); Z rate = 1/(2·T2) − 1/(4·T1) |
| Readout flip, per qubit | `readout_error` | Used as given (symmetric) |
| Reset flip, per qubit | none in the snapshot | 0; the sensitivity test T-L1 uses `readout_error` instead |
| CX duration | `cx` `gate_length`, all pairs | Mean over every directed pair on the device, after removals |
| H duration | `sx` `gate_length` | Mean over all qubits |
| Measurement duration | `readout_length` | Mean over all qubits |
| Reset duration | `reset` `gate_length` | Mean over all qubits |
| Snapshot provenance | `backend_name`, `last_update_date` | Stored as device id, capture time and source package version |

Every qubit on the device gets idle, readout and reset values, including qubits a placement does not use.

**Unrepresentable CX errors (rev 2).** The CX rule gives a total Pauli probability of 1.25 · e, which is a valid channel only for e ≤ 0.8; 0.8 is also the largest average gate error any two-qubit Pauli channel can have. A larger value cannot describe a working gate and is read as a failed calibration, so the coupler is removed from the device in both directions. The converter records each removed coupler and its raw values. On the pinned data this removes CX(5,8) and CX(19,20) on FakeHanoiV2 (gate_error 1 in one direction) and nothing on FakeJakartaV2.

## Benchmark specification

The benchmark covers two devices, four circuits and four noise conditions. Every valid placement is evaluated on three seeds with at least 200,000 shots each, and more where T-P4 is not met.

**Devices**

| Device | Qubits | Used for | Valid placements: distance 3 / 5 / 7 |
| --- | --- | --- | --- |
| FakeJakartaV2 | 7 | Circuits A, B, C under N0–N3 | 8 / does not fit / does not fit |
| FakeHanoiV2 | 27 | Circuits A, B, D under N1–N3 | 80 / 102 / 74 (rev 2; 100 / 164 / 178 before couplers CX(5,8) and CX(19,20) were removed) |

**Circuits** (all generated noiseless)

| ID | Circuit | Qubits |
| --- | --- | --- |
| A | Distance-3 bit-flip repetition code, memory experiment, 3 rounds; also run at 1 and 5 rounds | 5 |
| B | Distance-3 phase-flip repetition code (circuit A in the X basis), 3 rounds | 5 |
| C | Distance-2 repetition code, 3 rounds; reports M3 only | 3 |
| D | Distance-5 and distance-7 bit-flip repetition codes, 3 rounds | 9 and 13 |

**Noise conditions**

| ID | Condition | Expected outcome |
| --- | --- | --- |
| N0 | Zero noise | Every placement gives 0 detection events and 0 logical errors |
| N1 | Uniform: every parameter set to the device's median value | All placements statistically equivalent |
| N2 | Real: the converted snapshot | The main result; no expected value |
| N3 | N1 plus one planted fault: a qubit with readout error × 5, and separately a connection with CX error × 5. The fault must be avoidable by at least one valid placement, and is recorded in the config before running | The chosen placement avoids the fault |

## Test specification

Every test has a mechanically checkable pass criterion. None requires the noise-aware placement to win: performance tests pass when the comparison is computed and reported correctly, whatever the verdict.

| ID | Area | Test | Pass criterion |
| --- | --- | --- | --- |
| T-C1 | Correctness | Placement validity | 100% of returned placements satisfy R-P1, and Nove raises no error for them |
| T-C2 | Correctness | Circuit meaning preserved | Same detector and observable counts; 0 detection events in ≥ 10⁴ noiseless shots; mapping back reproduces the original circuit text exactly |
| T-C3 | Correctness | Enumeration complete | Candidate counts: 8 for circuits A and B and 14 for C on FakeJakartaV2; 80, 102 and 74 for distances 3, 5 and 7 on FakeHanoiV2 (rev 2); each matches an independent subgraph-isomorphism count |
| T-C4 | Correctness | Planted fault (N3) | The chosen placement avoids the planted qubit or connection, and its P\_L is no higher than any placement containing the fault, within 95% intervals |
| T-C5 | Correctness | No valid placement | A circuit needing a qubit with 4 neighbours raises a clear error, with no fallback |
| T-C6 | Correctness | Determinism | Identical inputs give an identical placement, and ties follow the documented rule |
| T-C7 | Correctness | Equivalence of merged placements | Any placements treated as equivalent have P\_L within each other's 95% intervals under N2 |
| T-C8 | Correctness | Selection without sampling | Selection completes with every sampler disabled (mocked to raise) |
| T-I1 | Integration | Converter values | Spot checks match the conversion rules exactly on both devices, e.g. on FakeJakartaV2 CX(4,5) total Pauli probability = 1.25 × 0.01342 and qubit 5 readout = 0.0555; both CX directions present |
| T-I2 | Integration | Snapshot hash | A JSON round trip gives an identical SHA-256 |
| T-I3 | Integration | Noise on mapped qubits | The noise-lowered circuit has two-qubit noise exactly on the mapped connections with the specified probabilities, and readout flips on the mapped qubits |
| T-I4 | Integration | Backend agreement | Nove's Stim and Python backends give P\_L within their combined 99% interval |
| T-I5 | Integration | Decoder construction | A decoder builds for every placement, and every results file records the approximate decoder model |
| T-I6 | Integration | Package checks | The package's test suite, linter and type checker pass |
| T-I7 | Integration | Second device | The full pipeline runs on FakeHanoiV2 with no code change and returns valid placements for circuits A, B and D |
| T-I8 | Integration | No Qiskit at runtime | The package imports and the benchmark runs in an environment without Qiskit installed |
| T-F1 | Fairness | Only the placement differs | A diff of run configs shows only the mapping changing |
| T-F2 | Fairness | Uniform-noise null (N1) | A homogeneity test across placements gives p > 0.01, and the report states no gain |
| T-F3 | Fairness | Seed separation | Logged evaluation seeds share no value with seeds used during selection |
| T-F4 | Fairness | Baseline committed first | The commit holding the fixed baseline and benchmark config predates the first N2 results |
| T-P1 | Performance | Main comparison | Chosen vs fixed and vs the all-placement average, as absolute and relative differences with 95% intervals and one verdict: better, indistinguishable or worse, computed from all seeds' shots pooled (rev 4) |
| T-P2 | Performance | Regret | M5 is reported for every device, circuit and noise condition |
| T-P3 | Performance | Score validity | M4 is reported, and a negative value is flagged |
| T-P4 | Performance | Precision | Wilson interval half-width ≤ 10% of P\_L for every placement under N2 and N3 |
| T-P5 | Performance | Per-round consistency | ε is reported for 1, 3 and 5 rounds of circuit A, and no two round counts give opposite verdicts for the same comparison; *indistinguishable* next to *better* or next to *worse* is consistent (rev 3) |
| T-P6 | Performance | Gain vs code distance | On FakeHanoiV2, the chosen-vs-average difference is reported with 95% intervals at distances 3, 5 and 7, with the trend stated: grows, shrinks or flat |
| T-R1 | Reproducibility | Same seed | Re-running gives bit-identical failure counts |
| T-R2 | Reproducibility | Different seeds | No two of the 3 seeds give opposite verdicts for the same comparison; *indistinguishable* next to *better* or next to *worse* is consistent (rev 3) |
| T-R3 | Reproducibility | Provenance | Every results file records the Nove commit; Stim, PyMatching and qiskit-ibm-runtime versions; source file SHA-256; calibration and device hashes; and all seeds |
| T-L1 | Limitations | Assumption sensitivity | N2 is rerun with CX duration at the device minimum and maximum, with reset error = readout error, and (rev 2) with every removed coupler restored, both directions at the CX rule applied to its lower raw `gate_error`; the report states whether the verdict holds |
| T-L2 | Limitations | Scope statement | The report's limitations section covers every item under Constraints |

## Reporting requirements

The report states every result as holding "under this noise model", never as a claim about hardware. It contains:

| ID | Requirement |
| --- | --- |
| R-R1 | M1–M3 with intervals for every device, circuit, noise condition and placement |
| R-R2 | One verdict per comparison: better, indistinguishable or worse; overlapping 95% intervals are reported as indistinguishable, not as a loss. The verdict is computed from all seeds' shots pooled, and the number of seeds giving the same verdict alone is shown next to it, e.g. "better (2/3 seeds), pooled: better" (rev 4) |
| R-R3 | Headroom per case: oracle best vs worst with intervals; where they overlap, the report states that placement does not matter for that case |
| R-R4 | M4 and M5 for every case |
| R-R5 | For each device and circuit under N2, a plot of all placements sorted by P\_L with interval bars, with the chosen and fixed placements marked |
| R-R6 | The decoder, its settings, and the fact that its model is approximate |
| R-R7 | Results of T-L1, stating whether each verdict holds under the alternative assumptions |
| R-R8 | A limitations section covering every item under Constraints |

## Constraints and scope

**Constraints** (facts about Nove and the data that results must disclose)

| ID | Constraint |
| --- | --- |
| K1 | Nove's `Experiment.to_detector_model()` raises a raw Stim `ValueError` when calibrated two-qubit noise has more than one non-zero Pauli term, so no exact detector model exists for these experiments; the decoder model is approximate |
| K2 | Nove sets durations per gate name, not per qubit pair, so per-pair CX timing differences (235–540 ns on FakeJakartaV2) are not represented |
| K3 | Nove's readout error is symmetric; the snapshots' asymmetric readout is averaged |
| K4 | Nove's gate noise is directional: CX(a,b) and CX(b,a) are separate entries |
| K5 | Nove applies idle noise to every qubit index up to the highest one used |
| K6 | Noise is a Pauli-channel approximation; coherent errors, crosstalk, leakage and drift are not modelled |
| K7 | The FakeJakartaV2 snapshot is dated 2024-05-27, after ibmq\_jakarta's retirement on 2023-09-26; both snapshots are realistic noise profiles, not live-device measurements |
| K8 | Both devices have at most 3 neighbours per qubit, so no surface code fits SWAP-free |

**Out of scope for v1**

- Routing and SWAP insertion
- Hardware runs
- Surface codes and square-grid devices
- Unevenness sweeps and model-mismatch robustness tests
- Qiskit's layout as a comparison method
- Changes to Nove, and the upstream bug report for K1

## Prohibited actions

The implementer must not:

| ID | Prohibited action |
| --- | --- |
| X1 | Modify Nove's code; Nove is used only as a dependency |
| X2 | File issues, open pull requests or post anything upstream, including about K1 |
| X3 | Make Qiskit a runtime dependency (R-D6) |
| X4 | Select placements by simulating their logical error rates (R-P4); the oracle is for evaluation only |
| X5 | View N2 results before the fixed baseline and benchmark config are committed (R-F4), or change either afterwards |
| X6 | Change the settled decisions, the noise conversion rules or any pass criterion; an unmet requirement is reported, not weakened |
| X7 | Drop or rerun placements, seeds or cases because of their results |
| X8 | Present results as hardware claims, or claim a gain the intervals do not support. A gain is claimed only when the pooled verdict is *better*, and is labelled *weak evidence* when not every seed shows *better* alone (rev 4) |
| X9 | Add anything listed as out of scope for v1 |
| X10 | Push to a shared remote branch |

## Sources and provenance

Pinned versions: Nove commit `485b413` (16 Aug 2026), `qiskit-ibm-runtime` 0.50.0 (FakeJakartaV2 props dated 2024-05-27; FakeHanoiV2 props dated 2025-02-26), Stim 1.16.0.

- [KUQCI/nove repository](https://github.com/KUQCI/nove)
- [IBM Quantum: Retired QPUs](https://quantum.cloud.ibm.com/docs/guides/retired-qpus): ibmq\_jakarta, 7 qubits, retired 2023-09-26
- [qiskit-ibm-runtime Fake Provider](https://quantum.cloud.ibm.com/docs/en/api/qiskit-ibm-runtime/fake-provider): FakeJakartaV2 and FakeHanoiV2
- QCI Open Projects document: the noise-aware compilation brief
