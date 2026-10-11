# Decisions (v2)

Choices the v2 spec leaves open, each with the reason. The v2 spec is binding; DECISIONS.md
still holds for everything v1. Spec issues found in review are in the project file
`v2-spec-review/REVIEW.md`; work they affect (Parts A, B's Qiskit baseline, C) waits for the
spec owner's call.

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

**Choice.** `place()` takes a `Scorer` (name, method text, score function); the default is
the v1 score, so every v1 call is unchanged. `scoring.SCORERS` lists the scorers the CLI and
API accept. The tie-break rule (DECISIONS.md D2) is the same for every scorer.

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

## V5. Decoder-graph score (R-G1, R-G2; not frozen yet)

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

**Freeze.** Waits for the spec owner's decision on the R-G7 rule (review issue 7) and on the
order of freeze and final runs (review issue 8).
