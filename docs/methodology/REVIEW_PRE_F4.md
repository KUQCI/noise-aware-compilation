# Review before the F3 re-freeze and held-out conversion (v1 to v3)

Nov 10, 2026 · reviewed at `main` `1a1db13`. File and line references (`file:line`) point to that commit; later commits have moved many of them.

**How this was done.** This thread wrote most of v3, so it is not an independent reviewer. The reading was therefore done by five fresh readers with no build history and none of my conclusions: one full pass on Khaled's laptop, and four in the cloud (design, claims, prior work, tools). I merged their findings and re-checked the numbers marked ✔ myself. No repo file was edited. Every reader was an AI model briefed by this thread, so the review is AI-assisted and not independent; nobody outside the project has reviewed v3.

**Corrected Nov 10 (SPEC_V3 revision 5).** A citation check against the sources changed four sentences (Kan et al., checked in the full text including Appendix B2; Le Fur et al.; IBM's AI passes; IBM's fake-backend guide) and the Aer note, and dropped one source (Davis, not peer reviewed). The findings and the verdict are as written.

**Corrected Nov 10 (fresh review of the whole project).** A second source check, run on Khaled's laptop, and a recomputation from the committed files changed these places: R4's interval ends at +0.027, not +0.028 (`nac_bench.analysis_v2.r_g7` on the three FakeAlgiers cases without d=7); R8 no longer cites Stim's notebook for `rounds=d * 3`, which could not be checked; the Aer note no longer says the model double counts relaxation, and the "Not checked" note on Nove's idles is answered; the Google note says what the 17% is a share of; the fake-backend quote is dropped because its page could not be reached; and the related-work lines on Le Fur et al., Kan et al. and Beverland et al. are more exact. The findings and the verdict still stand as written.

**Tags.** **[V]** verified: checked against the repo, or on a web page a reader opened. **[J]** judgment.

**X16 incident (my error).** [V] I left the X16 rule out of the tools reader's brief. It opened the props and conf files of FakeKingston, FakeAachen, FakeBerlin and FakeNighthawk inside a downloaded qiskit-ibm-runtime 0.52.0 wheel. By its own account it printed device-level aggregates only: medians of T1, T2, readout error (both directions), CZ and single-qubit error, gate and readout lengths, and counts of CZ entries at 1.0, above 0.8 and above 0.1. It says it saw no per-qubit or per-coupler value and no coupler position, and wrote nothing to disk. I cannot verify that account. The aggregates reached me; I deleted the wheel. No finding below uses them. Nothing was converted, scored or simulated. Section 1 of the spec must record this (B7).

## Verdict: NO-GO today; GO once B1 to B7 are done

[J] Nothing found invalidates the design. But the repo cannot run revision 4 as committed, and six things lock at conversion that cannot be added honestly afterwards. Together they are about two days of work.

## Change before freeze

**B1. The freeze record contradicts the spec, and HEAD cannot run revision 4.**
- [V] ✔ `configs_v3/score_freeze.json:5` and `held_out.json:119` say *k* = 10. `held_out.json` still has Qiskit level 1 (line 17), 400 failures and a 25.6M cap (108–109), FakeNighthawk in the family, and `beats_min_cases` 5 for both layouts (138).
- [V] `analysis_v3.py:274` still uses the Newcombe-based "holds", and lines 313–319 the old claim rules.
- [V] ✔ X18 (`SPEC_V3.md:189`) forbids any score change after F3, without the "after any held-out result" condition X17 has. Section 8 allows the reopening. The spec contradicts itself, and *k* = 640 also overrides revision 4's own fallback (80).
- [V] `tests/test_v3_freeze.py` asserts the freeze file equals the first F3 commit's, and anchors F4 on the commit that first added `held_out.json`. A conversion made before the revision-4 config would pass it.
- Fix: amend X18 to except the surface-code *k* before any conversion; give `freeze_f3.json` a first and a second commit; update config, runner, analysis and tests together; test for 52 family members; make the F4 test use the last commit touching `held_out.json`; log two deviations (*k*, and P2-4's "wording fixed in advance").

**B2. *k* = 640 is an in-sample choice resting on about 16 effective samples.**
- [V] ✔ FakeMiami's 128 d=5 candidates are 16 qubit sets × 8; 87% of P_L variance is between sets. On set means, ρ is 0.856 at *k* = 80 and 0.903 at 640.
- [V] A cluster bootstrap of that difference gives [0.007, 0.305].
- [V] ✔ 640 is the edge of the list: ρ is 0.947 at 1,280. At d=3, ρ falls as *k* grows (0.989 at 40, 0.956 at 1,280), and the score exceeds 1 there, so it is not converging.
- [V] The top d=5 choice is the same placement at *k* = 10 and from 40 to 640.
- [V] `SPEC_V3.md:88` still calls that ρ "context that no rule uses".
- [J] Keep 640; changing it again is worse than disclosing it. Fix: correct line 88; report R3-S3 as the tuning set; pre-declare *k* = 10 and *k* = 80 as sensitivity choices on FakeBerlin and FakeNighthawk.

**B3. The baselines cannot see the largest data-qubit error, so a win may only mean "knows T1/T2".**
- [V] Per round, a data qubit's idle error during the ancilla's measurement and reset exceeds its gate error on every CZ development device: FakeFez 0.0109 against 0.0051, FakeMarrakesh 0.0135 against 0.0043, FakeMiami 0.0073 against 0.0039.
- [V] Summed error excludes idling (`baselines.py:5-6`). mapomatic's default cost "does not include errors on idle qubits" ([paper](https://ar5iv.labs.arxiv.org/html/2209.15512)). VF2PostLayout uses "the avg error rate for each qubit and 2q link" ([docs](https://quantum.cloud.ibm.com/docs/api/qiskit/qiskit.transpiler.passes.VF2PostLayout)).
- [V] The v1 score, which has the same information but no decoder graph, picks the decoder-graph's placement in several reset cases (`RESET.md`).
- Fix: pre-register two secondary endpoints on the held-out devices: decoder-graph against an idle-aware summed error, and against the v1 score. Without them the report cannot say the decoder graph is what helps.

**B4. Every confirmatory test grades the score under the model it reads.**
- [V] Selection, simulation and decoder weights all come from one converted snapshot (`evaluation.py:93-98`).
- [V] Real drift already breaks it: the stale choice is worse than naive in 5 of 16 cases at reset = readout, by 127% on FakeHanoi d=7 after 14 days (`results_v3/drift`).
- [V] On hardware, calibration-based selection is known to miss: "context-dependent and coherent gate errors appear to dominate the divergence" ([Hartnett et al.](https://arxiv.org/abs/2404.06535)).
- Fix: pre-register one exploratory mismatch run on the family placements: select on the converted model, simulate with each error multiplied by an independent log-normal factor, decode with unperturbed weights.

**B5. The obvious competitor is missing: just simulate each placement.**
- [V] ✔ On v2's committed counts, choosing by a 100,000-shot sample and grading on the other seeds gives P_L within 2–3% of the score's choice at d=3 and d=5 (FakeAlgiers A: 3.78e-3 against 3.70e-3). At d=7 it is 29–35% worse (8.35e-5 against 6.18e-5).
- [V] Simulating every heavy-hex d=7 placement costs about 3.5 core-hours; only the square lattice at d=7 (12.8M placements) needs a score.
- Fix: R3-C6's pilot already samples every simulated placement with 100,000 shots. Pre-register "pilot-best" as an exploratory method, graded on main-stage counts, and reword T3-C5 so pilot counts may select but never grade.

**B6. Claims generalise further than the evidence, and their wording locks at conversion.**
- [V] p-values cover Monte Carlo noise only: one snapshot per device, 8 nested cases on 2 devices (1 for the square lattice). The thresholds 5 of 8 and 3 of 4 have no derivation (P2-4).
- [V] The naive placement sits at or below the 17th percentile of P_L in 22 of 28 development runs (20 of 28 at the 16th; corrected Nov 10), so it is usually a lucky draw, not a typical one.
- [V] The FakeMiami surface code is near threshold: best per-round error 1.41% at d=3 and 1.43% at d=5 (corrected Nov 10 from 1.43% and 1.47%, which were not computed by 1 − (1 − P_L)^(1/rounds)).
- Fix: word each claim as "on FakeKingston and FakeAachen, under this noise model", not "on this layout"; pre-register the all-placement mean and the choice's percentile beside every naive figure; pre-write the likely "no difference shown" reading for the surface code.

**B7. Section 1 is incomplete, and the cost projection is stale.**
- [V] It omits the FakeMiami surface simulations (P2-16), the exhaustive scores on 24 development cases (P2-18, P2-24), the *k* check (P2-27), the X16 incident above, and this review.
- [V] Both pilots assumed 400 failures and *k* = 10. The revised bound is 180 × 3 × 102.4M shots × 2.4 µs = 37 core-hours, under one wall hour; *k* = 640 is about 5 minutes on one core. The limits hold, but the 35.6-hour total was never updated, and surface per-shot cost is in neither pilot.
- Fix: write both into the spec and decision log.

## Change before the report

- **R1. v1 and v2 documents never point to the re-analyses that weaken them.** [V] README, REPORT.md and REPORT_V2.md do not mention v3. Add a banner. Suggested: "On FakeAlgiers at reset = readout the decoder-graph score beats the naive placement in 4 of 4 cases and beats summed error, Qiskit's preset and mapomatic in 1 of 4; the rest are ties."
- **R2. "40%" is one case.** [V] ✔ FakeAlgiers d=7 against naive, −40.5% [−46.3, −34.2]; Qiskit's preset and mapomatic pick the same placement there (`REANALYSIS.md:119`). "Matches or beats Qiskit" holds only narrowly, on three CX devices. [J] Retire both phrases.
- **R3. v2's "Qiskit VF2Layout" baseline is summed error counted twice.** [V] ✔ It picks summed error's placement in 9 of 10 cases.
- **R4. v2's default-score decision rests on one mirror pair at reset 0.** [V] Without d=7 the statistic is −0.016 [−0.060, +0.027].
- **R5. v1's FakeHanoi gains reverse at reset = readout, unflagged.** [V] ✔ A5 is −15.2% at `REPORT.md:38` and +6.0% worse at line 338. `SPEC_V3.md:165` ("v1's flip replicates") overstates: 1 of 3.
- **R6. Drift.** [V] `DRIFT.md:17` omits that summed error and mapomatic kept more than the score in 3 of 4 FakeFez circuits.
- **R7. Decoder.** [V] PyMatching added correlated matching in 2.3 ([README](https://github.com/oscarhiggott/PyMatching)). [J] Re-simulate the family placements with it, as exploratory; near-tied surface placements may reorder.
- **R8. Rounds.** [J] Three rounds at d=5 and d=7 is fewer than memory experiments usually run (about *d* rounds or more). Call results "3-round" and add a rounds = d sensitivity run.
- **R9. "Dead" couplers.** [V] IBM: an error of 1 means the value "is considered stale" and "is not an indication that the qubit or edge is necessarily non-working" ([QPU information](https://quantum.cloud.ibm.com/docs/en/guides/qpu-information)). Say "uncalibrated".
- **R10. README and provenance.** [V] ✔ Line 37 installs from `claude/project-thread-vef0cz`; line 8 says SPEC.md is "not edited" (it has three revisions); no LICENSE file; `SPEC_V2.md:228` links a private claude.ai page; "audit" and "review" name no reviewer. [V] ✔ All 59 commits name Khaled alone; the only disclosure is `DECISIONS_V2.md` V10.
- **R11. How the work was produced.** [V] arXiv: generative AI use "should be reported consistent with subject standards for methodology" and tools "should not be listed as an author" ([policy](https://info.arxiv.org/help/moderation/index.html)). APS: "disclose any substantive uses" ([policy](https://journals.aps.org/authors/ai-based-writing-tools)). [J] Add a README section and a methods paragraph: Khaled set the questions, made the decisions and owns the results; code, analysis and drafts were written with an AI coding assistant under his direction; the earlier "reviews" were AI-assisted, not independent.

## Noted

- [V] The 1.25*e* and 1.5*e* factors match the Pauli probabilities implied by Qiskit Aer's depolarizing conversion ([source](https://raw.githubusercontent.com/Qiskit/qiskit-aer/main/qiskit_aer/noise/device/models.py)). Aer splits a gate's error into thermal relaxation during the gate and a depolarizing remainder, so its total equals the reported error. This project charges the whole reported error as one Pauli channel and adds no relaxation during the gate: Nove idles an operated qubit only for the rest of its moment (`nove/schedule.py:379-381` at `485b413`), so its total also equals the reported error and relaxation is not counted twice. The double count is elsewhere: IBM's two-qubit error "includes the error of the layer of single-qubit Cliffords" ([QPU information](https://quantum.cloud.ibm.com/docs/en/guides/qpu-information)), while the circuits charge their own H gates again, so single-qubit error around each two-qubit gate is mildly counted twice. The error is also "Measured in 'isolation'", so parallel-layer effects are missing.
- [V] Readout is symmetric (`conversion.py:236`); FakeFez's two directions are 0.0115 and 0.0039.
- [V] Google's error budget attributes "an estimated 17%" of 1/Λ, the inverse of its error-suppression factor, to correlated errors ([paper](https://arxiv.org/html/2408.13687v1)); it is a share of that budget, not of all errors. Leakage and crosstalk are out of scope here (K6).
- [V] Nove idles a gated or measured qubit only for the rest of its moment, after its operation (`nove/schedule.py:379-381`; checked in the fresh review, Nov 10).
- [V] The z-test is valid (independent seeds, shots fixed by the pilot); Holm tolerates shared placements. [J] Shots and multiplicity control exceed what QEC papers report; sinter's own example stops at 5,000 errors.
- [V] The freeze order holds on commit times, which are self-asserted: history was rewritten and the remote has no tags.
- [V] Qiskit 2.6.2 is current (2.5.2 used). `run_v3.py:515-524` runs level 2 on one seed. [J] Record a 10-seed sweep.
- [J] Reset = readout as headline, the cross-fitted oracle, and best-found as a lower bound are sound.

## Related work

- **Noise-aware layout.** [Murali et al.](https://arxiv.org/abs/1901.11054v1); [mapomatic](https://arxiv.org/abs/2209.15512); tket [NoiseAwarePlacement](https://docs.quantinuum.com/tket/user-guide/_sources/manual/manual_noise.md.txt), a baseline a referee may expect; [Kurniawan et al.](https://arxiv.org/abs/2407.21462) on stale calibration. IBM's AI-pass guide names only the coupling map as input and does not mention noise ([docs](https://quantum.cloud.ibm.com/docs/en/guides/ai-transpiler-passes)).
- **Placement for QEC on IBM devices.** [Kan et al., Oct 2026](https://arxiv.org/abs/2610.23840) ran 88 usable d=3 surface-code placements on the IBM Nighthawk r2 processor ibm_phoenix and chose placements from this hardware ranking. The detection probability of each memory's check family correlates with five-round logical error at Spearman 0.86 (Z memory) and 0.89 (X memory), in memories run without reset or echo. The 88 are the usable ones of 96 SWAP-free placements, after placements containing one bad qubit were excluded (Methods .2); Appendix B2 gives the correlations as 0.856 [0.774, 0.907] and 0.895 [0.840, 0.930] (2,000 bootstrap resamples). Fig. 1b shows "44 placements with the illustrated orientation", which likely means 88 counting transposes, though the paper does not say so. Detection probability comes from the measured syndromes; the paper reports no correlation for any calibration-based predictor of logical error. This is the closest work and the benchmark a referee will cite. [Le Fur et al.](https://arxiv.org/abs/2605.02861) pick contiguous qubit subsets "from the latest calibration data" without defining "best"; only their colour code ran on IBM Boston, while their repetition code ran on IBM Kyiv and their Bell-state runs on IBM Fez. [Hetényi and Wootton](https://arxiv.org/abs/2404.15989): "we did not investigate systematically different locations".
- **The estimator.** [Fowler](https://arxiv.org/abs/1208.1334) and [Ghosh, Fowler and Geller](https://arxiv.org/abs/1210.5799) estimate logical error from lightest failing chains. [Beverland et al.](https://arxiv.org/abs/2511.15177) compute the minimum-weight failing configurations, which fix the onset of a fitted failure spectrum; that is the closest prior work on the estimator.
- **Fake backends.** Each fake backend is one dated calibration snapshot of an IBM QPU (its props file's `last_update_date`). The IBM local-testing guide this line quoted could not be reached at its address on Nov 10, so the quote is dropped.
- [J] No reader found a paper that ranks SWAP-free QEC placements by a sampling-free score from a calibration-derived detector error model. Frame the score as an application of a known estimator and say "first systematic comparison". Effect: claim wording and related work, plus B4; no conclusion changes.

## Final steps, ranked

**(a) Before the re-freeze and conversion**

| # | Step | Value | Effort |
|---|---|---|---|
| 1 | B1: make spec, freeze record, config, code and tests agree | essential | 1 day |
| 2 | B3: idle-aware summed error and v1 score as secondary endpoints | high | 2 hours |
| 3 | B5: "pilot-best" as an exploratory method | high | 1 hour |
| 4 | B6: claim wording, all-placement mean and percentile | high | 1 hour |
| 5 | B2: *k* wording and sensitivity choices | high | 30 min |
| 6 | B7: section 1 and cost projection | high | 30 min |
| 7 | B4: mismatch run | medium | half a day |

**(b) Before the v3 report:** R1 to R3, R10 and R11 (high value, an hour or two); R5, R6, R8 and R9 wording (medium, low); R7 correlated re-decode (medium, a day); R4 (low).

**(c) For a preprint**
1. Hardware runs on one Heron device: the score's choice against summed error, Qiskit level 2 and the naive placement, interleaved in one session. Very high value, 1–2 weeks, needs IBM access. Without them the claim stays "under a calibration-derived Pauli model".
2. A comparison with Kan et al.'s proxy (detection probability) and tket. Medium, low.
3. Rounds = d runs and a per-round figure. Medium, low.

**Skip:** AI routing as a baseline; d=7 surface codes; more drift pairs; another change of *k*.

Stopping here and waiting for Khaled.
