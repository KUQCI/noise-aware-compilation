# Roadmap evaluation and v3 plan

> Copied into the repository on Nov 10, 2026 from the project file `v3/EVALUATION.md` (written Nov 8), which `SPEC_V3.md` and `DECISIONS_V3.md` cite. Two changes: this note, and "dead couplers" now reads "uncalibrated couplers" (two-qubit error 1.0, which IBM marks as stale), as everywhere else in the repository. Its facts about the held-out devices are among those `SPEC_V3.md` section 1 lists. Later decisions in `DECISIONS_V3.md` supersede parts of the plan; for example, revision 5 moved the heavy-hex claims to FakeBoston and FakePittsburgh.

Nov 8, 2026 · evaluation of *Noise-Aware Placement: Roadmap and GitHub Guide* (Nov 9, 2026) against the v1 and v2 code and results. Revised the same day after Khaled's two reviews; what changed is listed in section 6.

## Summary

The roadmap holds up. Every number it quotes from our v1 and v2 results checks out within rounding (section 1), and its diagnosis is right: v2's held-out device could not test the layout, the oracle and verdict rules are biased, and real drift sits where v2's gain was mostly lost. The plan changes it in five ways:

1. **Keep two devices of each layout held out.** The 156-qubit heavy-hex map is shared by Fez, Marrakesh and Kingston (Heron r2) and Aachen, Boston and Pittsburgh (Heron r3); the 120-qubit square lattice by Nighthawk, Miami and Berlin. Development uses Fez and Marrakesh, and tunes the square-lattice search on Miami. Held out until the v3 freeze: Kingston and Aachen, and Berlin and Nighthawk. Nighthawk's file is synthetic (`backend_name` is `fake_nighthawk`), so Berlin is the real square-lattice test.
2. **Don't estimate "the best placement" from the decoder-graph score's own top 30.** That only finds placements the score already likes. Kingston and Aachen are simulated exhaustively, and Nighthawk d=3 too. At Nighthawk-class d=5 and d=7, the candidate set is the union of every method's top choices plus a random sample, called "best found", with the oracle cross-fitted and the random sample's size and its own best-found interval reported.
3. **Drop three items:** fitting a correction factor to the score (it stays labelled a ranking signal, in the CLI too), mixed repetition-code schedules (a two-layer schedule has to be uniform, so mixing needs a third CX layer and only adds idle time), and the claim that FakeAlgiers "couldn't separate the methods" (the decoder-graph score had 2 wins and 2 ties against summed error, both wins holding after Holm, and won the R-G7 test).
4. **Test reset error = readout error.** Every v1 and v2 headline used reset error 0, because IBM publishes none; IBM resets by measuring, so a wrong readout leaves a wrong state. v1's own check turned Hanoi d=3 gains into ties or losses. A pre-registered run (Phase 1b) repeats it for every method on all v2 cases, and **v3's headline uses reset = readout** (Khaled's choice), with reset 0 alongside.
5. **Stale decoder weights in the synthetic drift too**, and **exact versions in a lock file**, checked by rerunning two v2 cases on patty.

## 1. Claims checked against our data

Status: ✓ matches, ≈ matches within definition or rounding, ✗ differs, ? could not check here.

| Claim in the roadmap | What our data says | Status |
| --- | --- | --- |
| Decoder-graph choice is within 0–12% of the best placement on every 27-qubit case | Relative regret 0.000–0.121 (max: Hanoi d=7) | ✓ |
| It was 12–41% better than the naive placement on the held-out device | FakeAlgiers vs fixed: −12.3% (B) to −40.5% (d=7), all pooled *better* | ✓ |
| Every method came within 1.19× of the best on FakeAlgiers | Max ratio 1.189 (v1 score, summed error and Qiskit at d=7) | ✓, but see change 3 |
| VF2Layout picked the same placement as summed error in 9 of 10 cases | 9 of 10 (the exception is Hanoi d=5) | ✓ |
| On Hanoi d=5 the best placement ranked 32nd of 102 by summed error, the decoder-graph choice 41st | 32nd and 41st | ✓ |
| At d=7 the score is 0.30–0.81× the simulated error rate | 0.30–0.81 on Hanoi d=7 | ✓ |
| At δ = 0.5 the decoder-graph score kept 0.17–0.75 of its gain on Hanoi | Hanoi A 0.35, d=5 0.75, d=7 0.17 [−0.46, 0.57] | ✓ |
| Converted models have zero reset error, so v2's reset drift changed nothing | Reset flip is 0 in all three snapshots, and 0 × any factor is 0 | ✓ |
| A placement and its mirror differ by up to 44%; the score picks the better one in 105 of 109 clearly different pairs | With "clearly different" = non-overlapping 95% intervals: 117 pairs, score right in 111, largest gap 2.2e-4 vs 3.3e-4 (Hanoi d=7, 48% higher) | ≈ |
| The overlap rule contradicted the printed difference interval five times in v1 | 7 distinct comparisons in REPORT.md (1 in the N2 headline, 4 under planted faults, 2 in the sensitivity variants), and 3 more in REPORT_V2.md | ≈ (more than stated) |
| v2 makes 80 comparisons | 10 cases × 4 baselines × 2 scores = 80 | ✓ |
| 80 identical placements look "significantly different" 96% of the time | Expected range of 80 normal draws ≈ 4.8 SD vs a ≈ 3.9 SD non-overlap threshold, so roughly 93–96% | ≈ (plausible) |
| REPORT.md ≈ 53,000 words; REPORT_V2.md ≈ 18,000 words and ≈ 1,000 table lines | 53,564 words; 18,059 words and 1,006 table lines | ✓ |
| Snapshot dates: Jakarta 2024-05-27, Hanoi 2025-02-26, Algiers 2024-02-28 | Same, from `last_update_date` | ✓ |
| README facts (Python ≥ 3.10, Stim ≥ 1.15 < 2, PyMatching ≥ 2.2, Z clamped when T2 > 2·T1, durations averaged per device, couplers above 0.8 removed, reset 0) | All match `pyproject.toml` and `conversion.py` | ✓ |
| FakeKingston (156, heavy-hex, Heron r2) and FakeNighthawk (120, square lattice, up to 4 neighbours) ship in qiskit-ibm-runtime 0.50.0 with CZ | Both present, basis `cz, id, rz, sx, x`; Kingston dated 2026-04-15, Nighthawk 2025-12-08 | ✓ |
| Placement counts (directed paths of 5/9/13 qubits, before removing uncalibrated couplers): Hanoi 100/164/178, Kingston 856/2,662/7,790, Nighthawk 8,312/345,408/12,841,728 | All nine match. After removing uncalibrated couplers: Kingston 750/2,114/5,648 (7 couplers have CZ error 1.0); Nighthawk unchanged | ✓ |
| Older qiskit-ibm-runtime releases ship differently dated snapshots, and the drift table (Hanoi 14 days, Hanoi 9 months, Fez, Torino) | All four rows match to 2 decimals (49 releases scanned, 0.20.0–0.51.0). There is also a 2021-12-09 Hanoi snapshot. Kingston and Nighthawk have one snapshot each, so no real-drift pair exists for them | ✓ |
| mapomatic ranks by 1 − ∏(1 − error) over 2-qubit gates, `sx`/`x`, measurements and resets (resets at readout error), with no idle or T1/T2 term | Exactly that (`layouts.py:225-259`). It runs with Qiskit 2.5.2 on the fake backends, with deprecation warnings only | ✓ |
| Level 1 keeps a SWAP-free trivial layout with no noise check, otherwise VF2Layout with `call_limit=(50_000, 1_000)`, `seed=-1`, then Sabre; `routing_method="none"` fails loudly | All confirmed in the 2.5.2 source and by test. Level 2 uses (5M, 10k), level 3 (30M, 100k) plus VF2PostLayout | ✓ |
| `ibm_kingston` is on IBM's free plan | Can't check from here; plans change | ? |
| (not in the roadmap) FakeNighthawk is a real-device snapshot | Its `backend_name` is `fake_nighthawk`, unlike the others, so its numbers may be synthetic. That's fine for testing a different layout, but its results shouldn't be described as a real chip's | note |


## 2. Changes to the plan

Kept as written unless listed here.

| Roadmap item | Change | Why |
| --- | --- | --- |
| Step 2.3: develop the pre-filter on Kingston | **Develop on Fez + Marrakesh (heavy-hex) and Miami (square lattice). Hold out Kingston + Aachen and Berlin + Nighthawk until the v3 freeze** | Kingston has twins with the same coupling map, so nothing is lost by keeping it out; developing on it would make it development data. Two held-out devices per layout, one per Heron revision on heavy-hex |
| Step 2.4: "best" = best of the decoder-graph score's top 30 | **Kingston and Aachen: simulate every placement** (Kingston 750 / 2,114 / 5,648 at d=3/5/7 after removing uncalibrated couplers). **Nighthawk d=3: exhaustive** (8,312). **Nighthawk-class d=5/7: union of every method's top choices plus a random sample, "best found", cross-fitted, with the sample's size and its own best-found interval** | The roadmap's version only finds placements the score already likes. Pooling seeds does not remove the winner's curse, so the oracle is cross-fitted as in P1-2 |
| Step 2.4 (cost) | **Timing pilot on Fez before Kingston**; exhaustive *scoring* is feasible even at Nighthawk d=7 (12.84M placements × 41 ms ≈ 150 core-hours) | Kingston's full simulation is untested at this size |
| Step 2: Nighthawk results | Labelled "synthetic calibration" | Its file is synthetic |
| Step 3.2: real-drift pairs | Hanoi 14 days, Hanoi 9 months, Fez 1 month, **and Torino 12 months** (δ 0.7–0.95). No pair exists for Kingston or Nighthawk | Torino brackets the realistic range from above |
| Step 3.5: robust choice rule | Keep, pre-registered as a secondary analysis | Otherwise it becomes a garden of forking paths |
| Step 3: reset settings | **Moved out of drift into Phase 1b (below)**, as its own pre-registered run next to 6b | It questions the main results, not drift, and needs new simulation |
| Step 4.2: choose k "to reproduce today's rankings on all 10 v2 cases" | Keep; all 10 are development data (FakeAlgiers is spent), and the new score is frozen before any held-out device is converted | |
| Step 4.4: surface codes on Nighthawk | Distance 5 only: a rotated d=5 patch fits a 9×9 box on the 10×12 lattice, d=7 needs 13×13 | Avoids promising d=7 |
| Step 4.5: fit a correction factor | **Drop.** The score is a ranking signal, and the CLI keeps saying so | A fitted factor adds a model we'd have to validate too |
| Step 5.2: mixed repetition-code schedules | **Drop.** Keep 5.1 (one simulation to confirm the mirror mechanism; done, P1-10) | Neighbouring ancillas with different orders collide on their shared data qubit, so a two-layer schedule is either a placement's or its mirror's; mixing needs a third layer, which only adds idle time |
| Step 6.3: Holm correction | Apply to v1/v2 as a labelled re-analysis (done); pre-register the headline comparisons for v3 | Holm applied after the fact is honest only if called that |
| Step 6.1: "five" contradictions | Fix all 10 (7 in v1, 3 in v2) | Section 1 |
| Step 6.1: example interval −12.3% [−22.2%, −1.1%] | **Keep, naming it v1 FakeHanoi A1, v1 score vs fixed.** It is not a gain: v1 called it indistinguishable although the interval excludes zero; the new rule calls it better, but p = 0.03 is 0.13 after Holm | REPORT_V2.md has a different −12.3% [−17.2%, −7.1%] (Algiers B) |
| Step 6b: mapomatic | The real package through a small adapter that serves the converted snapshot as `properties()`/`configuration()` (done) | A re-implementation would invite "you got it wrong" |
| Step 6b: Qiskit default transpile | Fixed `seed_transpiler`; a routed result would be reported as "not SWAP-free" rather than dropped (none occurred) | Keeps the comparison honest |
| README: in-memory `place()` taking a Qiskit circuit | An **optional** Qiskit import, loaded only when a Qiskit circuit is passed; v1's T-I8 restated as "the package installs and runs without Qiskit" | T-I8's purpose is the no-Qiskit install, which this keeps |
| Front-page claim that FakeAlgiers "couldn't separate the methods" | Reword to "headroom among the methods was small: the decoder-graph score had 2 wins and 2 ties against summed error" | Both wins hold after Holm (Phase 1). Against v2's VF2Layout baseline it is also 2 and 2; against Qiskit's default at level 1 it is 1 win, 2 ties, and the same placement at d=7 |
| "Keep v1 and v2 frozen" | Kept in substance. The authorship rewrite regenerated both reports with bit-identical counts; only commit hashes in provenance changed | Your "Rewrite everything" choice |

## 3. Additions

1. **Phase 1b, reset error = readout error, pre-registered.** All 10 v2 cases at reset = readout, plus A1 and A5 (1 and 5 rounds) on Hanoi and Algiers at both settings. Every method selects under the model it is graded in; Qiskit's default and mapomatic get a Target whose reset carries the readout error. Its Holm family repeats Phase 1's 40 headline comparisons in the other setting (P1-11, P1-12).
2. **Reset = readout is v3's headline setting**, reset 0 alongside on every headline device (P1-13).
3. **Stale decoder weights in the synthetic drift as well** (v2 Part A re-graded), so synthetic and real drift are comparable.
4. **A lock file** (`requirements-lock.txt`) with every package version, the interpreter, and the Rust toolchain that builds Nove's core. Stim's x86-64 wheel ships only SSE2 and generic builds, so a wider SIMD unit doesn't change which code runs; the file records which build loaded. patty reruns two v2 cases: identical counts are the expectation, and if they differ, intervals must agree and v3 runs entirely on patty so no comparison mixes machines.
5. **Machine and per-stage timing in every run file**, plus `nice -n 19` and a 48-thread cap on patty, since it's shared.
6. **A score-vs-simulated scatter for every device**, with Spearman's ρ in the title, also for the summed-error and v1 scores.
7. **Nove already models crosstalk, leakage and drift processes.** IBM's files don't calibrate them, so they stay off, but a limitation can say the simulator supports them.

## 4. Order of work

| Phase | Work | Where | Needs simulation? |
| --- | --- | --- | --- |
| 0 | Authorship rewrite; rerun v1 and v2 and confirm bit-identical counts; push | here | rerun only (done) |
| 1 | Step 6 statistics and 6b baselines on existing counts; machine provenance; lock file; Step 5.1 mirror check | here | one d=7 pair (done) |
| 1b | Reset = readout run, pre-registered | here | yes, small |
| 2 | SPEC_V3.md: questions, endpoints, Holm family at reset = readout, decision rules, the device split above, freeze order, and what was already seen of the held-out devices (coupling maps, dates, uncalibrated-coupler counts) | here | no |
| 3 | Step 4 scaled score, developed on v1/v2 data and Fez/Marrakesh; freeze | here + patty | development only |
| 4 | CZ converter and native CZ circuits (developed on Fez/Marrakesh); timing pilot on Fez; then Kingston/Aachen exhaustive and Nighthawk/Berlin (d=3 exhaustive, d=5/7 best found) | patty | yes |
| 5 | Step 3 real drift with stale decoder weights, at both reset settings | patty | yes |
| 6 | Surface code d=5 on the square lattice with schedule candidates | patty | yes |
| 7 | Summary report over v1–v3 following the report rules; README rewrite | here | no |

## 5. What I need from you

1. **GitHub settings.** `main` now exists and carries everything. Please make it the default branch and fill in the About box (the roadmap's text is fine), then tell me whether to delete the `claude/*` branches; a full backup bundle is in `/mnt/project-files/backups/`.
2. **Real hardware (the roadmap's Step 7 is missing from the document).** If you want it, I'll need an IBM Quantum account and API token later. Nothing in phases 0–7 depends on it.

## 6. What changed after review

- Device split: one development and one held-out pair per layout instead of Fez alone; Nighthawk d=3 exhaustive; Berlin added as the real square-lattice test.
- "Best found" is cross-fitted and reports the random sample's size and interval; a timing pilot on Fez comes before Kingston.
- Mixed schedules are dropped for the right reason (a third layer is needed), and the Algiers wording names the 2 wins and 2 ties against summed error (against Qiskit's default at level 1 it is 1 win, 2 ties and one shared placement).
- The lock file covers the interpreter and the Rust toolchain. The proposed fallback of running on the laptop when patty's counts differ was not adopted: the laptop loads the same SSE2 Stim build, so it would not settle anything, and keeping all of v3 on one machine does.
- The reset setting is a pre-registered run of its own (Phase 1b), and reset = readout is v3's headline setting.
- The Step 6 example interval is kept, with its case named and without calling it a gain.
