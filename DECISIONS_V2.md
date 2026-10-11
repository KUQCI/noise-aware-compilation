# Decisions (v2)

Choices the v2 spec leaves open, each with the reason. The v2 spec (SPEC_V2.md, revision 1)
is binding; DECISIONS.md still holds for everything v1. The pre-implementation review's
fixes were approved by the spec owner and are SPEC_V2.md revision 1, made before any v2
result existed.

## V1. Where v2 files live (T2-V1, X14, X15)

**Choice.** v2 code that needs no Qiskit is in the package (`api.py`, `cli.py`,
`scoring.py`, `baselines.py`). The Qiskit baseline will live in a benchmark-only module
outside `src/`. v2 configs, results and report get their own locations; `configs/`,
`results/runs/` and `REPORT.md` stay v1's.

**Why.** Three v1 tests would otherwise fail without any v1 behaviour changing: T-I8's test
rejects any `import qiskit` line under `src/`; T-F4's test requires every v1 results commit
to descend from the last commit touching `configs/`; the results tests read every file in
`results/runs/` and `REPORT.md`.

## V2. Scorers (R-G3)

**Choice.** `place()` takes a `Scorer` (name, method text, score function). `scoring.SCORERS`
lists the scorers the CLI and API accept, and `scoring.DEFAULT_SCORER` is their default, set
by R-G7 to `decoder-graph` (REPORT_V2.md section 4). The CLI (`nac place`) and the R-I4 API
(`api.place_circuit`) both use `DEFAULT_SCORER`, so they agree (R-I5, T2-I3). The tie-break
rule (DECISIONS.md D2) is the same for every scorer.

**`place()` keeps the v1 score as its default.** `place()` is v1's library function
(placement.py), not one of the two R-I4 operations. It keeps its v1 default so every v1 call
and result is unchanged (X14), and because placement.py is frozen with the decoder-graph score
(R-G4; changing it would break T2-G2). Passing `scorer=DECODER_GRAPH_SCORER` selects the new
score there.

## V3. Command line and API (Part D)

* **Commands.** `nac convert`, `nac place`, and `nac example`, which writes the shipped
  example (a distance-3 bit-flip repetition code and FakeJakartaV2's IBM file pair) so the
  README example (R-I9) needs no repository checkout. `python -m noise_aware_compilation`
  runs the same CLI.
* **Stored converted device.** The layout the package already uses for its own devices:
  `<name>_snapshot.json`, `<name>_device.json` and `hashes.json`. `--device` accepts such a
  directory or the name of a shipped device (`jakarta`, `hanoi`). The name defaults to the
  backend name in the files.
* **Mapping file.** No timestamp and no absolute paths (input files are named by file name
  and SHA-256), so the CLI and API files are byte-identical (T2-I3).
* **Errors (R-I7).** The converter is unchanged (R-G5). The API checks every field the
  converter reads before calling it and raises `InputError` with a one-line message naming
  the missing field. The CLI prints `nac: error: <message>` and exits with code 1;
  `--debug` re-raises. Argument errors exit with argparse's code 2.
* **Version.** The package version is 0.2.0.

## V4. Lowest summed error (R-B4)

**Choice.** An operation's total error probability is its Nove channel's total (sum of the
Pauli probabilities for a gate, readout flip for a measurement, reset flip for a reset), read
from the noise model the method is given. Sums use `math.fsum` (correctly rounded, so
independent of operation order), which makes T2-B1's hand calculation match bit-exactly.
Scores are compared exactly; ties go to the lexicographically smallest mapping.

## V5. Decoder-graph score (R-G1, R-G2, R-G4)

**Choice.** `decoder_graph.py` (module docstring has the full definition): from the placed
circuit's decomposed approximate detector error model, build the matching graph, find
every minimum-length closed path through the boundary that flips a logical observable, and
add up, per path, the probability that the flipped edges outweigh half the path under the
decoder's weights log((1 − p)/p), plus any undetectable logical error. That is the
leading-order failure probability of a minimum-weight matching decoder (a union bound over
logical paths). The graph is built in the package's own code, so `place` needs no decoder
(R-I6).

**Development.** Only on FakeJakartaV2 and FakeHanoiV2, against v1's committed N2
measurements (`scripts/develop_decoder_graph_score.py`, output in
`results_v2/development/decoder_graph.txt`). One variant was tried and dropped: adding paths
up to 2 edges longer than the minimum overcounted (scores 1.5–4.8× the measured P_L instead
of 0.4–1.0×) and lowered M4 on 8 of 10 cases. FakeAlgiers has not been converted or scored.

**Freeze (R-G4, R-G8).** Frozen at commit `b441685`, before FakeAlgiers was converted and
before any final v2 run. The annotated tag `v2-decoder-graph-freeze` names that commit in
the implementer's checkout, but pushing tags was refused by the session's network policy, so
`configs_v2/freeze.json` records the commit and the git blob of each frozen file
(`decoder_graph.py`, and `placement.py`, which defines how a scorer is applied). T2-G2 and
T2-G3 check against that file. To publish the tag: `git tag -a v2-decoder-graph-freeze
b441685 && git push origin v2-decoder-graph-freeze`.

## V6. Drift (R-S2 to R-S6)

* **Channels.** One factor exp(δz) per channel: each directed CX's Pauli channel, each
  qubit's H channel, each qubit's idle rates (X, Y and Z together), readout flip and reset
  flip. Caps (SPEC_V2.md rev 1): a Pauli channel whose total would exceed 1 is scaled down
  proportionally to total 1; a flip is capped at 0.5. Every capped channel is recorded per
  draw.
* **Common random numbers.** Draw k of a device uses the same deviates z at every δ (seed =
  `derive_seed("drift-v2", device, k)`), so the strengths differ only in scale. This removes
  draw-to-draw noise from the comparison across δ and does not change any single strength's
  distribution.
* **δ = 0.** Every factor is exactly 1 and the provenance is kept, so the seen snapshot is
  bit-identical to the true one and has the same hash (R-S6). The δ = 0 records are still
  stored, as the check that the drift path reproduces the true-model choices (T2-S1).
* **Grading.** A seen-model choice is graded with the true-model measurement of that
  placement (every valid placement is measured once per case), decoded with true-model
  weights (K9).

## V7. Qiskit baseline (R-B5 to R-B8)

`nac_bench/qiskit_layout.py` (outside `src/`, so T-I8 still holds). The Target's instruction
errors are the Nove channel totals the summed-error baseline uses; durations come from the
Nove device; T1 and T2 are recovered from the idle rates. `VF2Layout` runs with `seed=-1`,
`strict_direction=True` and no call, trial or time limit, which makes it deterministic; the
result is checked against the candidate set. Qiskit is pinned to 2.5.2 (`bench` extra).

## V8. v2 runner and seeds (Parts A to C)

* **Seeds.** Base seeds 20261021–20261023, disjoint from v1's; evaluation chunk seeds are
  `derive_seed("eval-v2", base, case, mapping, chunk)`, so no v2 shot reuses a v1 seed
  (checked in `tests/test_v2_results.py`). v1's precision rule decides shots per seed.
* **One measurement, many selections.** Each case measures every valid placement once under
  the true model; every method's choice, under the true model and under each of the 4 × 20
  seen models, is a lookup into those measurements. Drift therefore adds selections, not
  shots.
* **Clean provenance.** As in v1, the runner refuses uncommitted changes to code, data or
  config, and every results file records the commit it ran from. The final runs ran from a
  clean checkout at `797e798`; the analysis and report code was written in a separate
  worktree while they ran, so the runs' `package_git_dirty` is false.

## V9. Analysis and report (R2-R1 to R2-R7)

* **Part B.** Every score's choice against every baseline with v1's rules: pooled verdict
  with the per-seed tally, a gain only from a pooled *better*, labelled weak evidence when not
  every seed agrees.
* **M6.** Mean over draws of (P_L fixed − P_L seen choice) ÷ (P_L fixed − P_L true choice),
  95% interval from 4000 bootstrap replicates (seed 20261031) that resample draws and redraw
  each placement's failures binomially. Reported only where that method's δ = 0 choice is
  pooled *better* than fixed; otherwise "no gain to retain".
* **M7.** Draws whose choice is pooled *worse* than fixed, with a Wilson interval over draws.
* **Development devices.** Every FakeJakartaV2 and FakeHanoiV2 result is labelled a
  development-device result (R2-R7); only FakeAlgiers answers question 3.
* **R-G7.** Mean over the four FakeAlgiers cases of (P_L graph choice − P_L v1 choice) ÷
  P_L of the best placement, with a delta-method interval from shot noise only (the four
  cases are fixed, not a sample). The decoder-graph score becomes the default only if the
  interval lies wholly below zero. The verdict covers only these four cases.
