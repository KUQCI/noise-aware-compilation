# Decisions

Choices SPEC.md leaves open, and departures the data forced, each with the reason.
SPEC.md is binding; nothing here changes a settled decision, conversion rule or pass
criterion. Departures are reported, not hidden (X6).

## D1. Scoring method

**Choice.** The score of a placement is the total probability of the fault mechanisms in
Stim's approximate detector error model (`approximate_disjoint_errors=True`) of the
relabelled circuit after Nove has lowered the calibrated hardware noise into it. It is the
expected number of detector-visible faults per shot; lower is better.

**Why.** It uses only the circuit, the device and the noise model (R-P4) and needs no
sampling (T-C8 mocks every sampler to raise). Building it through Nove's own lowering means
the score sees exactly the noise the simulation will apply, including timing-dependent idle
noise, readout and the direction of each CX (K4). Counting only detector-visible mechanisms
ignores faults that cannot affect the code (for example Z errors in the bit-flip code), which
a raw sum of calibration errors would count. It is deliberately simple; it ignores how faults
combine into logical errors, and M4 (Spearman between score and simulated P_L) measures how
well it ranks placements.

## D2. Tie-break rule

**Choice.** Scores are rounded to 12 significant digits; the lowest rounded score wins and
ties go to the lexicographically smallest mapping (the tuple of physical qubits in circuit-
qubit order).

**Why.** Rounding makes floating-point noise in otherwise identical scores (uniform noise N1,
mirror images) a tie rather than an arbitrary winner. The lexicographic rule is deterministic
and is the same order that defines the fixed baseline, so under N1 the chosen placement is the
fixed placement and the "no gain" result is exact.

## D3. CX gate error of 1 (conversion departure)

**Fact.** FakeHanoiV2 has two directed CX entries, CX(19,20) and CX(5,8), with
`gate_error = 1` (the opposite directions are normal). The rule gives 1.25·1/15 per term,
summing to 1.25, which is not a probability distribution; Nove rejects it.

**Choice.** Cap each of the 15 terms at 1/15 (total 1). The pair stays in the device
connectivity, so the SPEC placement counts (100/164/178) are unchanged.

**Why.** This is the smallest change that keeps every other value exactly as the rule gives
it; the gate stays "always wrong", which is what an error of 1 means. Recorded in the
snapshot's `conversion_notes`, in DECISIONS.md and in the report's limitations.

## D4. Negative Z idle rate (conversion departure)

**Fact.** Five FakeHanoiV2 qubits (2, 5, 10, 11, 13) have T2 > 2·T1, for which
1/(2·T2) − 1/(4·T1) is negative; Nove's `PauliRate` rejects negative rates.

**Choice.** Clamp the Z rate to 0; X and Y rates are unchanged.

**Why.** Smallest change that Nove can execute; the affected rates are tiny in magnitude
(the clamped values were between −8e-5 and −7.8e-4 per µs). Recorded in the snapshot, here and in the report.

## D5. Seed scheme

**Choice.** Three base seeds (20261011, 20261012, 20261013) are fixed in
`configs/benchmark.json`. Shots are run in chunks of 100,000; each chunk's Stim seed is
`sha256("eval|<base_seed>|<case_id>|<mapping>|<chunk>")[:8] & (2**63 − 1)`. Selection uses
no seeds at all (the score is deterministic), and every results file logs
`selection_seeds = []` and every chunk seed.

**Why.** Each placement, seed and chunk gets an independent stream (the statistics assume
independent binomials), every number is reproducible from the config (T-R1), the per-
placement run configs differ only in the mapping (T-F1; the seed is a fixed function of the
mapping, not a config entry), and evaluation and selection seeds are disjoint by construction
(R-F3, T-F3). The `eval` prefix domain-separates them from any future use.

## D6. Phase-flip circuit (circuit B)

**Choice.** Circuit B is circuit A conjugated by Hadamards: data are prepared with `R` then
`H`; each round the ancillas get `H`, then `CX ancilla→data` to each neighbour, then `H`, then
`M`; the data get `H` before the final `M`. The observable is data qubit 0.

**Why.** Only `R`, `H`, `CX` and `M` are used, so every operation maps onto a converted
calibration entry (H noise from `sx`, CX per direction); `RX`/`MX` would bypass the H
channel the conversion specifies. Conjugating CX by H on both qubits reverses it, so circuit B
uses the opposite CX directions from circuit A, which makes it a real test of K4.

## D7. Planted faults for N3

**Choice.** FakeJakartaV2: readout fault on qubit 0, CX fault on connection (0,1).
FakeHanoiV2: readout fault on qubit 1, CX fault on connection (1,2). The CX fault multiplies
both directions' Pauli probabilities by 5. A placement "contains" a qubit fault when it uses
the qubit, and a connection fault when a circuit CX runs on that connection.

**Why.** Each fault is used by the fixed baseline of every circuit on its device (so the test
is not trivial) and is avoidable by at least one valid placement (the runner checks this
before running). The faults are fixed in the committed config.

## D8. Precision rule and shot counts (T-P4)

**Choice.** 200,000 shots per seed to start. Under N2 and N3 (and the T-L1 reruns of N2), all
placements of a case are doubled together until every placement's pooled Wilson half-width
is at most 10% of its P_L, capped at 25.6 million shots per seed. N0 and N1 use 200,000.

**Why.** Doubling the whole case keeps the shot count identical across placements (R-F1). The
rule depends only on interval width, not on which placement wins, and it was fixed in the
config before any N2 run, so it is not a rerun because of results (X7). T-P4 is judged on
the pooled (three-seed) P_L, which is the reported value.

## D9. Homogeneity test for T-F2

**Choice.** Pearson chi-square test of homogeneity on the k × 2 table of logical failures
and successes across all valid placements (pooled over the three seeds), k − 1 degrees of
freedom. Also applied to circuit C, whose P_L is computed even though only M3 is reported.

**Why.** It is the standard test that k binomial proportions are equal, it uses every
placement at once, and with ≥ 200,000 shots per seed all expected counts are large.

## D10. Verdicts, intervals and trend

* **Verdict (R-R2).** "better"/"worse" only when the chosen placement's 95% interval does not
  overlap the reference's; otherwise "indistinguishable". Chosen vs fixed compares two Wilson
  intervals; chosen vs average compares the chosen Wilson interval with the average's normal
  interval (variance Σ p(1−p)/n / k²).
* **Differences (T-P1).** Chosen − fixed: Newcombe hybrid-score interval; relative: Katz log
  interval for the ratio. Chosen − average: exact variance of that linear combination, normal
  interval; relative: delta method.
* **Distance 2 (circuit C).** Reports M3 only; its comparisons, headroom, M4 and M5 use M3
  (lower is better) with a shot-clustered normal interval.
* **Trend (T-P6).** "flat" when the d = 3, 5, 7 chosen-vs-average intervals all overlap one
  another; otherwise "grows" if |Δ| at d = 7 exceeds |Δ| at d = 3, else "shrinks".
* **M5 rank.** 1 = lowest simulated P_L; ties share the average rank.

## D11. Sensitivity reruns (T-L1)

**Choice.** Each N2 case is rerun end to end (selection and evaluation) with (a) the CX
duration set to the device's minimum directed-pair `gate_length`, (b) the maximum, and (c)
reset flip probability equal to each qubit's readout error. The verdict "holds" when both
comparisons give the same verdict as the base N2 run.

**Why.** Rerunning selection too answers the question "would the method reach the same
conclusion under the alternative assumption", which is what a reader of the verdict needs.

## D12. Equivalent placements (R-F5, T-C7)

**Choice.** No placements are merged; mirror images and every other valid mapping are
simulated separately.

**Why.** Simulation is cheap enough to evaluate every placement, so merging is unnecessary,
and T-C7's condition then holds vacuously.

## D13. Package layout and stored data

* The fake-backend files are copied by the dev-only `scripts/fetch_device_files.py` into
  `data/devices/` with `SHA256SUMS`. The converted snapshots and device models live in the
  package (`noise_aware_compilation/device_data/`) with their SHA-256 in `hashes.json`; loading
  verifies the hashes. Qiskit appears only in the `dev` extra.
* Nove's `HardwareNoiseModel.from_calibration` maps the snapshot to the executable noise
  model; extra snapshot fields (`source_*`) keep the raw values for audit and are ignored by
  Nove.
* `nove` is pinned as `nove @ git+https://github.com/KUQCI/nove@485b413`.

## D14. Reading of T-P5 ("the verdict's direction is the same for all three")

**Choice.** Read strictly: both verdicts (vs fixed and vs average) must be identical at 1, 3
and 5 rounds of circuit A. On FakeHanoiV2 the chosen-vs-fixed verdict is *indistinguishable*
at 1 round and *better* at 3 and 5 rounds, so T-P5 is reported as **unmet** there. No verdict
points the opposite way (no *worse*). A looser reading ("no two verdicts point in opposite
directions") would pass, but choosing it after seeing the result would weaken a pass
criterion (X6). The test is kept and marked `xfail(strict=True)` so the suite stays runnable
while recording the failure; it turns red if the criterion ever starts passing.

## D15. N1 pairwise verdicts

Under N1 the chosen placement is fixed by the tie-break (all scores equal), so it carries no
information. T-F2's pass criterion is the homogeneity test; the report states "no gain" for
every N1 case whose homogeneity p-value exceeds 0.01. Two N1 pairwise chosen-vs-average
verdicts are not *indistinguishable* (FakeHanoiV2 A1 *better*, FakeJakartaV2 A5 *worse*);
they are reported as computed and explained as chance fluctuations of one fixed placement
against a very tight average interval, not claimed as gains or losses.
