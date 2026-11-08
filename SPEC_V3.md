# Noise-Aware Placement for Nove: v3 Spec

Nov 8, 2026 · @Khaled Al Daqrouq

**Revision 0 (Nov 8, 2026). Written before any v3 device was converted and before any v3 result on a held-out device existed.** It turns the roadmap (*Noise-Aware Placement: Roadmap and GitHub Guide*, Nov 9, 2026), as amended by the evaluation in the project file `v3/EVALUATION.md` and Khaled's two reviews, into requirements, tests and decision rules. Everything this spec settles is fixed before the held-out devices are converted (freeze order, section 8); later changes are listed in a revision note at the top with their date and reason, and in `DECISIONS_V3.md`.

**Revision 1 (Nov 8, 2026), from building F2 on the development devices; no held-out device was converted.** R3-C1 now states the H rule exactly (v1's rule from the `sx` error, not "one `sx` error") and the reset duration for a props file without a `reset` entry (FakeMiami). R3-C2 states the H cancellation rule the circuits use. T3-C1 to T3-C3 cover all three CZ development devices, and T3-C2 adds a distance check. R3-C7 adds FakeMiami d=7, the square-lattice timing proxy for FakeNighthawk d=7 (8,493,720 placements, against Nighthawk's 12.8 million).

## 1. What was seen before this spec

A pre-registration is only as good as its list of what was already known. Before this spec was written:

- **Every v1 and v2 result**, and the Phase 1 re-analysis of them (`results_v3/reanalysis/REANALYSIS.md`), which is post hoc.
- **The Phase 1b reset run** (`results_v3/reset/RESET.md`), pre-registered on its own and complete: 23 of its 40 headline comparisons hold after Holm at reset = readout.
- **Of the held-out devices (FakeKingston, FakeAachen, FakeBerlin, FakeNighthawk):** their coupling maps, snapshot dates, qubit counts, native gate sets, and how many couplers carry a two-qubit error of 1.0 (Kingston 7, Aachen 2, Berlin 6, Nighthawk 0); FakeKingston's placement counts after removing those couplers (750 / 2,114 / 5,648 at d=3/5/7); and that FakeNighthawk's file names itself `fake_nighthawk`, so its calibration is synthetic. No other calibration value of a held-out device was read, no held-out device was converted, and nothing was scored or simulated on one.
- **Of the development devices (FakeFez, FakeMarrakesh, FakeMiami, FakeTorino):** the same facts, plus the real-drift table in the roadmap (Step 3).

## 2. Goal and definition of done

v3 asks four questions:

1. **Layout:** does the decoder-graph score still choose better placements than the methods people use, on two chip layouts it was never developed on, with a different native gate (CZ)? (Part A)
2. **Drift:** how much of its gain survives real calibration change, when the decoder also only has the old calibration? (Part B)
3. **Scale:** can a faster version of the score rank surface-code placements, and does its choice beat the naive placement and summed error in simulation? (Part C)
4. **Use:** can someone outside the project see what it does and run it in a few minutes? (Part D)

v3 is done when every test in section 9 passes, the report meets section 10, and every v1 and v2 test still passes. No question has a required answer: a correctly measured loss is an acceptable outcome, and the report gives it the same prominence as a win.

## 3. Settled decisions

v1's and v2's specs stay in force except where this table or a later section changes them.

| Topic | Decision |
| --- | --- |
| Reset error | **Headline setting: each qubit's reset error equals its readout error** (Khaled's choice, P1-13). Every headline device also runs at reset 0, reported alongside as the bridge to v1 and v2 |
| Verdict rule | *Better* when the upper end of the 95% Newcombe interval for P_L(choice) − P_L(reference) is below zero, *worse* when its lower end is above zero, otherwise *tie* (P1-1). Same placement = tie |
| Headline test | Two-sided pooled two-proportion z-test, Holm's step-down correction at α = 0.05 over the one family in section 7. Everything outside it is exploratory |
| Oracle | Cross-fitted (P1-2): for each of the three base seeds, the best placement on the other two seeds, measured on the held-out seed |
| Naive placement | The first valid placement in lexicographic order (v1's and v2's *fixed* baseline) |
| Baselines | Naive placement; lowest summed error; Qiskit's default transpile at level 1 (headline), levels 2 and 3; mapomatic 0.14.0; v2's VF2Layout baseline with CZ in its target (exploratory) |
| Native gates | CX devices keep CX circuits. CZ devices get circuits built natively from CZ and H; CX is never lowered into CZ after the fact |
| Development devices | FakeJakartaV2, FakeHanoiV2, FakeAlgiers (spent in v2); FakeFez and FakeMarrakesh (156-qubit heavy-hex, Heron r2); FakeMiami (120-qubit square lattice); FakeTorino (real drift only) |
| Held-out devices | **Heavy-hex:** FakeKingston (Heron r2) and FakeAachen (Heron r3). **Square lattice:** FakeBerlin (real calibration) and FakeNighthawk (synthetic calibration, labelled so everywhere) |
| Not used | FakeBoston and FakePittsburgh (same map as Kingston) stay untouched, as a reserve for a later version |
| Scores | The v2 decoder-graph score, frozen since v2, is the headline score for repetition codes. The scaled score (Part C) is frozen before any held-out conversion and is the headline score for surface codes |
| Compute | Held-out and drift runs on patty, at `nice -n 19`, in the home directory, with at most 48 worker threads; all held-out runs use one machine and one environment (`requirements-lock.txt`) |
| Seeds | New and disjoint from every v1, v2 and v3 Phase 1 seed; fixed in the configs at freeze step F4 |
| Claims | Stated "under this noise model"; Nighthawk results are also labelled synthetic |

## 4. Part A: new layouts and CZ (roadmap Step 2)

| ID | Requirement |
| --- | --- |
| R3-C1 | The converter accepts CZ devices. A CZ gate error *e* becomes 15 Pauli terms of 1.25 *e* / 15 each, the v1 CX rule; IBM lists `cz` in both directions and each direction is stored as its own channel. H follows v1's rule from the published `sx` error *e*<sub>sx</sub> (three Pauli terms of 1.5 *e*<sub>sx</sub> / 3); `rz` is error-free. Couplers whose two-qubit error exceeds 0.8 are removed, as for CX. A props file with no `reset` entry (FakeMiami) gets a reset duration of the mean readout length plus the mean `x` length, a measurement followed by a conditional flip; on FakeFez this rule gives its published reset length exactly (1,560 + 24 = 1,584 ns), and the conversion notes record it |
| R3-C2 | The repetition codes A (d=3 bit-flip), B (d=3 phase-flip), D5 and D7 (bit-flip), 3 rounds each, are built natively for CZ devices: each CX layer of the v2 circuit becomes three layers, H on the CX targets, CZ on the same pairs, H on the targets. Then, until nothing changes: an H on a qubit and an H on the same qubit in the next layer are both removed (no time passes between them); two consecutive H-only layers on disjoint qubits are merged; empty layers are dropped. An H pair with any other layer between them stays, so the phase-flip code's data idle in the X basis as in v2's circuit B, and B stays a different circuit from A. The bit-flip round becomes H on the ancillas, CZ to each neighbour, H again. The CZ layers keep v2's order and pairs, so a placement uses the same couplers in the same layers; the H layers are their own layers, with idle noise on every waiting qubit |
| R3-C3 | The converter and the CZ circuits are developed and tested on FakeFez, FakeMarrakesh and FakeMiami only. A unit test checks the CZ circuit's noiseless detectors are deterministic and its logical observable is correct |
| R3-C4 | **Selection is exhaustive.** Every method scores every valid placement, at every distance on every device, including FakeNighthawk d=7 (12.8M placements; about 150 core-hours with the v2 score). If the timing pilot (R3-C7) projects more than 24 hours on patty for one case, that case's decoder-graph choice comes from the local search of R3-C8 instead, and the report says so |
| R3-C5 | **Evaluation.** On the heavy-hex devices and at d=3 on the square lattice, every valid placement is simulated. At d=5 and d=7 on the square lattice, the simulated set is the union of: every method's choice; the top 30 by each of the decoder-graph score, summed error and mapomatic's cost; and a uniform random sample of 100 valid placements (seeded). Its cross-fitted best is reported as *best found*, with the random sample's size, mean and best-found interval beside it |
| R3-C6 | **Shots.** A pilot stage runs every simulated placement once with 100,000 shots on a separate pilot seed. Each placement's main-stage shots per seed are then the smallest 200,000 × 2^k with 3 × shots × p̂ ≥ 400 (about a 10% relative half-width), capped at 25,600,000; p̂ = 0 gives the cap. Only main-stage counts, on three base seeds, enter any result |
| R3-C7 | A timing pilot on FakeFez d=7 (100 random placements, full selection and main-stage simulation) and on FakeMiami d=7 (enumeration of its 8,493,720 placements and the score on 1,000 random ones, the square-lattice proxy for FakeNighthawk d=7) is run before any held-out conversion; its projection for each held-out case is recorded in `DECISIONS_V3.md`. If FakeKingston or FakeAachen exhaustive simulation projects more than 48 hours on patty, that device falls back to R3-C5's best-found set, and the report says so |
| R3-C8 | A fast search (pre-filter and local search, roadmap Step 2.3) is developed on FakeFez, FakeMarrakesh and FakeMiami against their exhaustive scores, frozen at F3, and reported on the held-out devices as exploratory: its choice's score rank and P_L against the exhaustive choice |
| R3-C9 | Qiskit's default and mapomatic read an IBM-shaped Target (P1-5) with `cz` in place of `cx`, and each qubit's reset at its readout error in the reset = readout setting; v2's VF2Layout baseline gets `cz` at its Nove channel total |

## 5. Part B: real drift (roadmap Step 3)

| ID | Requirement |
| --- | --- |
| R3-D1 | Snapshots come from older `qiskit-ibm-runtime` releases, each installed in its own environment; each props file's hash and source release is recorded. Pairs: FakeHanoi 2025-02-12 → 2025-02-26 (14 days) and 2024-05-27 → 2025-02-26 (9 months); FakeFez 2025-01-23 → 2025-02-26 (1 month); FakeTorino 2024-02-28 → 2025-02-26 (12 months) |
| R3-D2 | Every method chooses from the old snapshot and is graded by simulation under the new one. **The decoder's weights come from the old snapshot** (a real decoder only has the old calibration). A second grading with new-snapshot weights is reported as context |
| R3-D3 | Circuits: A, B, D5, D7 on each device (CX on Hanoi, CZ on Fez and Torino), at both reset settings, reset = readout first |
| R3-D4 | Metrics: the absolute gain kept, P_L(naive) − P_L(choice from the old snapshot), both under the new snapshot, with a 95% interval; the gain a fresh choice would have had (choice from the new snapshot); and M6, their ratio, only where the fresh gain's interval has a lower end at least half its estimate (P1-4) |
| R3-D5 | v2's synthetic drift (Part A) is re-graded with stale decoder weights too, so synthetic and real drift are comparable; this is the only rerun of v2 data and it writes new files |
| R3-D6 | **Secondary, pre-registered:** a robust rule that chooses by the mean score over FakeHanoi's two older snapshots (2024-05-27 and 2025-02-12), graded on 2025-02-26, against "best on the latest older snapshot" (2025-02-12) |

## 6. Part C: the scaled score and surface codes (roadmap Steps 4 and 5)

| ID | Requirement |
| --- | --- |
| R3-S1 | The scaled score replaces the exact score's two exponential parts: the distribution of a chain's flipped weight is a dynamic program over edge weights log((1−p)/p) rounded to a grid, and chains are the top *k* by weight from Dijkstra on the doubled graph (detector, observables flipped so far) followed by a k-shortest-paths search. It uses no sampling |
| R3-S2 | *k* and the grid step are chosen on development data only: the smallest *k* (from 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000) for which, on all 10 v2 cases, the scaled score's top choice equals the exact score's and Spearman's ρ between the two over all candidates is at least 0.99. If none does, the *k* with the largest minimum ρ is used, and the report says the criterion failed |
| R3-S3 | The scaled score is checked against simulated P_L on FakeMiami surface codes (d=3 and d=5) before the freeze; the result is reported, and no threshold is applied |
| R3-S4 | Surface codes: the rotated surface code, Z-memory, distance 5, 5 rounds, native CZ, on the square lattice. Candidates are every SWAP-free embedding of the patch (shifts, rotations and reflections avoiding removed couplers), each with the standard stabilizer gate orders (N- and Z-shaped for each stabilizer type, the hook-safe combinations only) as schedule candidates |
| R3-S5 | Surface-code baselines: the naive placement with the default schedule; summed error (schedule-blind, so it takes the default schedule); Qiskit's default at level 1 and mapomatic (layout only, default schedule) |
| R3-S6 | The score's whole d=5 surface-code selection on one patty core takes at most 60 seconds; the time is reported |
| R3-S7 | The score is presented as a ranking signal, not an error rate, in the CLI output, the API docstrings and the report. No correction factor is fitted |
| R3-S8 | Dropped from the roadmap, with reasons in `v3/EVALUATION.md`: mixed repetition-code schedules (a two-layer schedule must be uniform, so mixing needs a third layer) and the correction factor |

## 7. The headline family and the claim rules

**Family `v3-headline`, 72 comparisons, at reset = readout:**

- 64: the v2 decoder-graph score against the naive placement, lowest summed error, Qiskit's default at level 1 and mapomatic, on circuits A, B, D5 and D7 on each held-out device (FakeKingston, FakeAachen, FakeBerlin, FakeNighthawk).
- 8: the scaled score against the same four references, on the d=5 surface code on FakeBerlin and FakeNighthawk.

Every other comparison (reset 0, levels 2 and 3, v2's VF2Layout baseline, the v1 score, the fast search, the all-placement average, drift, development devices) is exploratory and labelled so.

**Claim rules**, applied per layout (heavy-hex: Kingston and Aachen; square lattice: Berlin and Nighthawk) and per reference, to the family's verdicts after Holm:

| Wording | Condition |
| --- | --- |
| "beats *X* on this layout" | better after Holm in at least 5 of the layout's 8 repetition cases, and worse after Holm in none |
| "loses to *X* on this layout" | worse after Holm in at least one case; every such case is named |
| "no difference shown against *X*" | anything else |

For the surface code, per device and reference: "beats" when better after Holm, "loses to" when worse after Holm, otherwise "no difference shown". A case with no valid placement on a device leaves the family, which shrinks accordingly, and the report names it. Claims about real chips cite FakeKingston, FakeAachen and FakeBerlin; FakeNighthawk supports claims about the layout only.

**Secondary endpoints, pre-registered but not in the family:** cross-fitted relative regret of each method on every held-out case; Spearman's ρ between each score and simulated P_L per held-out case; the drift metrics of R3-D4.

## 8. Freeze order

| Step | What is frozen | Check |
| --- | --- | --- |
| F1 | This spec | Committed and pushed |
| F2 | CZ converter and native CZ circuits, developed on development devices | Tests T3-C1 to T3-C3 pass |
| F3 | Scaled score (*k*, grid) and fast search | Tagged commit `v3-score-freeze`; no later change to `decoder_graph.py` or the search |
| F4 | Run configs with every seed, then conversion of the held-out devices | Configs committed before the converted held-out files; converted files' hashes recorded |
| F5 | Every held-out and drift run, by the frozen code, on patty | Every run file records a commit that contains F3 and F4 |

## 9. Test specification

| ID | Test | Pass criterion |
| --- | --- | --- |
| T3-V1 | Regression | Every v1 and v2 test passes |
| T3-C1 | CZ conversion | On FakeFez, FakeMarrakesh and FakeMiami, every CZ channel equals 1.25 *e* / 15 per term for its published error, every H channel is 1.5 *e*<sub>sx</sub> / 3 per term, couplers above 0.8 are removed (7, 13 and 7), FakeMiami's reset duration follows R3-C1, and the CX devices' stored snapshots are unchanged byte for byte |
| T3-C2 | CZ circuits | Noiseless sampling of each CZ circuit gives deterministic detectors and the expected observable; the CZ circuit's two-qubit gate pattern equals the CX circuit's on the same qubits; under uniform noise its shortest undetectable logical error has the code's distance |
| T3-C3 | CZ Target | The P1-5 Target for FakeFez, FakeMarrakesh and FakeMiami equals the fake backend's own Target on every instruction the conversion kept |
| T3-C4 | Exhaustive selection | Every method's choice is among the candidates and, for the scores, has the best score among all candidates (ties broken lexicographically) |
| T3-C5 | Shots rule | Every placement's main-stage shots follow R3-C6 from its recorded pilot count; pilot counts appear in no result |
| T3-S1 | Scaled score agreement | The R3-S2 criterion is computed and recorded for every *k* tried |
| T3-S2 | No sampling | Selection with the scaled score completes with every sampler mocked to raise |
| T3-S3 | Speed | R3-S6 holds on patty |
| T3-D1 | Stale weights | Every drift-graded run records the old snapshot's hash as the decoder's source and the new one's as the simulator's |
| T3-F1 | Freeze order | The commits of F1, F3 and F4 are ancestors of every held-out run file's commit, and no held-out converted file exists in any commit before F4's configs |
| T3-F2 | Family | The `v3-headline` family has exactly 72 members, computed from the run files by committed code |
| T3-R1 | Reproducibility | Re-running a case with the same seeds gives bit-identical counts and selections |
| T3-R2 | Cross-machine | Two v2 cases rerun on patty give bit-identical counts to the committed ones, or, failing that, every placement's interval overlaps its committed interval; the outcome is recorded |
| T3-R3 | Provenance | Every v3 run file records the v2 provenance fields plus the machine (P1-9), the reset setting, the stage timings and the lock-file hash |
| T3-I1 | Interface | `place()` accepts a Qiskit circuit when Qiskit is installed, and the package installs, imports and runs both CLI commands without Qiskit (v1's T-I8, restated) |
| T3-I2 | README | The README's quickstart and Python example run as written in a clean environment |

## 10. Reporting requirements

The v3 report and the summary report over v1 to v3 follow the roadmap's report rules: a one-screen summary with a claims table and a forest plot; one question per section, answered first; spec IDs only in a traceability appendix; each number once; the verdict vocabulary *better*, *worse*, *tie*; a main body of at most about 4,000 words and 8 figures and tables; limitations and deviations in the main body; losses and ties as prominent as wins. In addition:

| ID | Requirement |
| --- | --- |
| R3-R1 | Every table and figure says whether it shows development or held-out devices, and which reset setting |
| R3-R2 | Every FakeNighthawk number is labelled synthetic calibration |
| R3-R3 | The Phase 1b reset result (v1's flip replicates for the v1 score; the decoder-graph score avoids it) is in the main body |
| R3-R4 | Best-found results name the size of the simulated set and the random sample's best-found interval, so a reader can judge how far the true best could be |

## 11. Constraints, scope and prohibited actions

**Constraints** (v1's and v2's still apply; the report discloses these)

| ID | Constraint |
| --- | --- |
| K12 | Reset = readout treats a reset failure as a readout error followed by a wrong flip; real resets may fail differently (leakage, measurement-induced transitions) |
| K13 | FakeNighthawk's calibration is synthetic |
| K14 | Real drift is measured between dated snapshots, each of which carries its own single-calibration noise |
| K15 | Best-found regret on the square lattice at d=5 and d=7 is a lower bound on the true regret |

**Out of scope for v3:** hardware runs (roadmap Step 7, which needs an IBM Quantum account), ECR devices, surface codes on heavy-hex devices, d=7 surface codes (they need a 13×13 region; the square lattice is 10×12), routing and SWAP insertion, changes to Nove.

**Prohibited actions** (v1's X1–X10 and v2's X11–X15 still apply)

| ID | Prohibited action |
| --- | --- |
| X16 | Convert, score or simulate a held-out device before F4, or read any of its calibration values beyond what section 1 lists |
| X17 | Change the family, the claim rules, the shots rule or the seeds after any held-out result exists |
| X18 | Change either score or the fast search after F3 |
| X19 | Mix machines within a held-out device's results |
| X20 | On patty: use anything outside the home directory, run above `nice -n 19`, or use more than 48 threads |
