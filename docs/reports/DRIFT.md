# Real calibration drift

Calibration changes from day to day, so a placement chosen on today's calibration is run on a chip that has already moved. In this study we chose placements on an older IBM calibration snapshot and graded them on a newer one, as someone who had not recalibrated would. We also re-graded v2's synthetic drift in the same way.

The study is exploratory but pre-registered: we committed both configurations before any run (`ab0fc85`, `e9742eb`). The 38 runs ran on one machine (patty) at the clean commit `ac177ab`, after the score freeze and the held-out configuration. Only development devices are involved, and nothing here is part of the main v3 test.

This page is a rewrite, for readability, of the reports our scripts generated. The originals ([DRIFT.md](https://github.com/KUQCI/noise-aware-compilation/blob/v3.0-results/results_v3/drift/DRIFT.md) and [UNCALIBRATED.md](https://github.com/KUQCI/noise-aware-compilation/blob/v3.0-results/results_v3/drift/uncalibrated/UNCALIBRATED.md)) are kept unchanged at tag `v3.0-results`, and every number here comes from them.

## What we found (reset = readout, the main setting)

1. **After a month on FakeFez, every old choice still beats the fixed placement, but the decoder-graph score keeps only about half its fresh gain in two of the four circuits.** Every method's old choice beats the fixed placement in all four circuits. The decoder-graph score keeps 17% to 46% (M6 from 0.53 to 1.27, with 0.55 in B and 0.53 in D5). Summed error and mapomatic kept more than the score in three of the four circuits (B, D5 and D7: 37% to 64% against 17% to 42%; at D5 the intervals overlap).
2. **On FakeHanoi at distance 7, the decoder-graph score's old choice becomes worse than the fixed placement.** It is 127% worse after 14 days and 76% worse after 9 months. Decoded with the new calibration it is still 76% and 51% worse, so the placement itself degraded, not only its decoder. Several of its twelve couplers drifted up over the 14 days; for example, IBM's CX error on 15–18 went from 0.0154 to 0.0224, and on 19–22 from 0.0074 to 0.0116. At distance 5 it keeps 28% (14 days) and 45% (9 months). At distance 3 both the old and the fresh gains are small (0% to 9%).
3. **After 12 months on FakeTorino, three of the decoder-graph score's four old choices use two couplers that have since become uncalibrated.** Decoded with the old weights, which still trust those couplers, they fail badly: circuit A's P_L is 0.32, for example, against the fixed placement's 0.059. Decoded with the new weights, with those couplers at 0.8, the same placements are 15% better, 5% worse and 37% better than the fixed placement (A, D5, D7). So most of the harm comes from decoding with old weights, not from the placement. Section 2 grades these placements at the couplers' older values too.
4. **Choosing again is what matters.** The decoder-graph score's fresh choice beats the fixed placement by 27% to 96% in every case except FakeHanoi's two distance-3 circuits, where its fresh gain is only 1% to 9%.
5. **The other methods lose their gains too.** mapomatic's old choice is worse than the fixed placement in all four FakeTorino circuits. On FakeHanoi B, summed error's, mapomatic's and Qiskit's choices are worse than the fixed placement by 116% to 129%, old and fresh alike; that repeats v2's finding on this circuit rather than showing an effect of drift. Summed error kept more of its gain than the decoder-graph score in three of the four FakeTorino circuits. We infer, but have not tested, that the decoder-graph score concentrates on the very best couplers, and on FakeTorino those are the ones that later became uncalibrated.
6. **A robust rule, which chooses by the mean score over FakeHanoi's two older snapshots, against the decoder-graph score's old choice (14 days).** At reset = readout it is better in A and D7, worse in D5 and tied in B. At reset 0 it is better in A and D5 (A only with new weights), and the two rules pick the same placement in B and D7. Eight cases on one pair of snapshots are too few to choose between the two rules.

At reset 0 the pattern is the same. The decoder-graph score's old choice keeps 25% to 61% on FakeFez; it becomes worse than the fixed placement at distance 5 and 7 after 14 days and at distance 7 after 9 months on FakeHanoi; and it uses uncalibrated couplers in B, D5 and D7 on FakeTorino. Its fresh choice beats the fixed placement by 31% to 97% outside FakeHanoi's distance-3 circuits, and by 0% to 15% there.

**v2's synthetic drift, re-graded.** v2 decoded every drifted choice with the true model's weights, so its decoder knew about drift that its placement had not seen. With stale weights instead, the decoder-graph score's v2 result holds, while summed error and v2's Qiskit baseline lose part of their gain on FakeJakarta A (section 3).

## How we measured

For each pair of snapshots, every method chooses a placement on the old snapshot (the *old choice*) and on the new one (the *fresh choice*). Both are simulated under the new calibration. The old choice is decoded with the old snapshot's weights, which is what someone who has not recalibrated would run (*old weights*), and the same samples are also decoded with the new snapshot's weights (*new weights*).

The snapshot pairs are FakeHanoi after 14 days (2025-02-12 → 2025-02-26) and after 9 months (2024-05-27 → 2025-02-26), FakeFez after 1 month (2025-01-23 → 2025-02-26), and FakeTorino after 12 months (2024-02-28 → 2025-02-26).

**Couplers that became uncalibrated.** A coupler that is valid in the old snapshot and reported with an error of 1.0 in the new one is simulated at a two-qubit error of 0.8, the converter's limit. IBM reports 1.0 when a coupler's value is stale, which does not mean the coupler is broken, so the real error is unknown in both directions: if it is near the old value, 0.8 overstates the harm, and if the coupler is out of service, 0.8 understates it. The harm shown for these placements is therefore neither an upper nor a lower bound. Section 2 grades them again at the older value.

**Metrics:**

- **Kept gain:** P_L(fixed) − P_L(old choice), as a share of the fixed placement's P_L, both decoded with old weights. Positive means the old choice still beats the fixed placement. Brackets give the 95% Newcombe interval. The fixed placement is the first valid placement in index order on the old snapshot. The tables call it *naive*.
- **Fresh gain:** the same comparison for the fresh choice against the fresh fixed placement, both decoded with new weights.
- **M6:** kept gain ÷ fresh gain, given only where the fresh gain is stable (its interval's lower end is at least half its estimate). Brackets give a 95% bootstrap interval.

**Qiskit is left out of the tables.** The drift configuration used Qiskit at level 1. On FakeFez and FakeTorino it chose TrivialLayout, which is the fixed placement, in all 32 of its selections, so its rows say nothing; on FakeHanoi, VF2Layout chose. We found this level-1 problem in our review of the plan on Nov 9, after the drift configuration was fixed, so we did not add a level-2 baseline after the fact.

## 1. Every case

One row per method and case. A dash means the metric is undefined: there is no fresh choice (the robust rule), or the fresh gain is not stable (M6).

### Reset = readout

| Pair | Circuit | Naive P_L | Method | Kept gain (old weights) | Kept gain (new weights) | Fresh gain | M6 | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| FakeFez, 1 month | A | 7.10e-03 | Decoder-graph | +46% [+42%, +49%] | +43% | +38% | 1.27 [1.11, 1.45] |  |
|  |  |  | Summed error | +17% [+13%, +21%] | +21% | +17% | 1.05 [0.73, 1.49] |  |
|  |  |  | mapomatic | +22% [+18%, +26%] | +19% | +17% | 1.36 [1.00, 1.88] |  |
|  |  |  | v1 score | +37% [+33%, +41%] | +42% | +43% | 0.91 [0.79, 1.04] |  |
| FakeFez, 1 month | B | 1.06e-02 | Decoder-graph | +32% [+29%, +36%] | +28% | +60% | 0.55 [0.49, 0.61] |  |
|  |  |  | Summed error | +44% [+41%, +47%] | +41% | -2% | – |  |
|  |  |  | mapomatic | +44% [+41%, +47%] | +41% | -2% | – |  |
|  |  |  | v1 score | +57% [+54%, +60%] | +63% | +63% | 0.92 [0.86, 0.99] | same choice old and new |
| FakeFez, 1 month | D5 | 9.33e-04 | Decoder-graph | +17% [+6%, +28%] | +18% | +34% | 0.53 [0.18, 0.98] |  |
|  |  |  | Summed error | +37% [+26%, +47%] | +35% | +53% | 0.74 [0.50, 1.01] |  |
|  |  |  | mapomatic | +37% [+28%, +47%] | +37% | +52% | 0.76 [0.55, 1.02] |  |
|  |  |  | v1 score | +37% [+28%, +47%] | +43% | +53% | 0.75 [0.54, 1.02] |  |
| FakeFez, 1 month | D7 | 1.26e-04 | Decoder-graph | +42% [+33%, +51%] | +27% | +52% | 1.07 [0.79, 1.42] |  |
|  |  |  | Summed error | +64% [+56%, +73%] | +58% | +52% | 1.63 [1.31, 2.06] |  |
|  |  |  | mapomatic | +64% [+56%, +73%] | +58% | +54% | 1.56 [1.26, 1.97] |  |
|  |  |  | v1 score | +47% [+39%, +56%] | +42% | +58% | 1.08 [0.84, 1.38] |  |
| FakeHanoi, 14 days | A | 5.75e-03 | Decoder-graph | +0% [+0%, +0%] | +0% | +9% | 0.00 [0.00, 0.00] | old choice is naive |
|  |  |  | Summed error | -2% [-7%, +2%] | -7% | +7% | – |  |
|  |  |  | mapomatic | -2% [-7%, +2%] | -7% | +9% | -0.24 [-0.91, 0.28] |  |
|  |  |  | v1 score | -2% [-7%, +2%] | -7% | -7% | – | same choice old and new |
|  |  |  | Robust rule | +6% [+1%, +10%] | +7% | – | – |  |
| FakeHanoi, 14 days | B | 3.92e-03 | Decoder-graph | +0% [+0%, +0%] | +0% | +1% | – | old choice is naive |
|  |  |  | Summed error | -129% [-136%, -122%] | -129% | -126% | – |  |
|  |  |  | mapomatic | -129% [-136%, -122%] | -129% | -126% | – |  |
|  |  |  | v1 score | +1% [-5%, +6%] | +1% | +1% | – | same choice old and new |
|  |  |  | Robust rule | +1% [-5%, +6%] | +1% | – | – |  |
| FakeHanoi, 14 days | D5 | 1.56e-03 | Decoder-graph | +28% [+19%, +36%] | +30% | +51% | 0.54 [0.37, 0.73] |  |
|  |  |  | Summed error | +27% [+19%, +36%] | +31% | +31% | 0.86 [0.55, 1.30] | same choice old and new |
|  |  |  | mapomatic | +27% [+19%, +36%] | +31% | +31% | 0.86 [0.55, 1.29] | same choice old and new |
|  |  |  | v1 score | +27% [+19%, +36%] | +31% | +31% | 0.86 [0.55, 1.28] | same choice old and new |
|  |  |  | Robust rule | -62% [-73%, -52%] | -28% | – | – |  |
| FakeHanoi, 14 days | D7 | 1.49e-04 | Decoder-graph | -127% [-144%, -110%] | -76% | +29% | -4.58 [-7.14, -3.32] |  |
|  |  |  | Summed error | -4% [-14%, +7%] | -3% | +11% | – |  |
|  |  |  | mapomatic | -170% [-189%, -151%] | -113% | +11% | – |  |
|  |  |  | v1 score | -170% [-189%, -151%] | -113% | +20% | -8.54 [-16.82, -5.63] |  |
|  |  |  | Robust rule | -84% [-96%, -72%] | -32% | – | – |  |
| FakeHanoi, 9 months | A | 6.18e-03 | Decoder-graph | +9% [+5%, +14%] | +4% | +5% | – |  |
|  |  |  | Summed error | +9% [+5%, +14%] | +4% | +4% | – | same choice old and new |
|  |  |  | mapomatic | -11% [-16%, -6%] | -12% | +5% | – |  |
|  |  |  | v1 score | +14% [+10%, +19%] | +5% | -12% | – |  |
| FakeHanoi, 9 months | B | 4.11e-03 | Decoder-graph | +4% [-2%, +9%] | +3% | +3% | – | same choice old and new |
|  |  |  | Summed error | -119% [-126%, -112%] | -128% | -128% | – | same choice old and new |
|  |  |  | mapomatic | -118% [-125%, -111%] | -127% | -128% | – |  |
|  |  |  | v1 score | +4% [-2%, +9%] | +3% | +3% | – | same choice old and new |
| FakeHanoi, 9 months | D5 | 1.86e-03 | Decoder-graph | +45% [+38%, +52%] | +45% | +50% | 0.99 [0.79, 1.23] |  |
|  |  |  | Summed error | -24% [-33%, -16%] | -3% | +38% | -0.70 [-1.02, -0.43] |  |
|  |  |  | mapomatic | -32% [-41%, -23%] | -6% | +38% | -0.92 [-1.29, -0.64] |  |
|  |  |  | v1 score | +8% [-0%, +16%] | +23% | +38% | 0.22 [-0.01, 0.47] |  |
| FakeHanoi, 9 months | D7 | 1.51e-04 | Decoder-graph | -76% [-92%, -61%] | -51% | +27% | -3.24 [-5.35, -2.21] |  |
|  |  |  | Summed error | +11% [+1%, +21%] | +5% | +6% | – |  |
|  |  |  | mapomatic | +16% [+6%, +26%] | +12% | +6% | – |  |
|  |  |  | v1 score | +11% [+1%, +21%] | +5% | +5% | – | same choice old and new |
| FakeTorino, 12 months | A | 5.86e-02 | Decoder-graph | -439% [-442%, -437%] | +15% | +93% | -4.95 [-5.01, -4.89] | uses 2 uncalibrated couplers |
|  |  |  | Summed error | +32% [+30%, +33%] | +52% | +77% | 0.43 [0.41, 0.45] |  |
|  |  |  | mapomatic | -41% [-43%, -39%] | +2% | +77% | -0.56 [-0.58, -0.54] |  |
|  |  |  | v1 score | -20% [-21%, -18%] | -23% | +92% | -0.22 [-0.24, -0.21] |  |
| FakeTorino, 12 months | B | 5.99e-02 | Decoder-graph | +89% [+88%, +90%] | +89% | +88% | 1.09 [1.07, 1.11] |  |
|  |  |  | Summed error | +38% [+37%, +40%] | +53% | +76% | 0.54 [0.52, 0.56] |  |
|  |  |  | mapomatic | -36% [-37%, -34%] | -4% | +76% | -0.50 [-0.53, -0.48] |  |
|  |  |  | v1 score | -17% [-19%, -16%] | -18% | +77% | -0.24 [-0.26, -0.22] |  |
| FakeTorino, 12 months | D5 | 7.54e-03 | Decoder-graph | -504% [-511%, -496%] | -5% | +90% | -6.64 [-6.90, -6.39] | uses 2 uncalibrated couplers |
|  |  |  | Summed error | -46% [-50%, -41%] | -7% | +73% | -0.75 [-0.83, -0.67] |  |
|  |  |  | mapomatic | -46% [-50%, -41%] | -7% | +73% | -0.75 [-0.83, -0.67] |  |
|  |  |  | v1 score | -64% [-69%, -59%] | -30% | +73% | -1.05 [-1.14, -0.96] |  |
| FakeTorino, 12 months | D7 | 2.78e-03 | Decoder-graph | -153% [-162%, -144%] | +37% | +96% | -2.15 [-2.34, -1.98] | uses 2 uncalibrated couplers |
|  |  |  | Summed error | +54% [+48%, +60%] | +59% | +84% | 0.87 [0.76, 0.98] |  |
|  |  |  | mapomatic | -26% [-33%, -19%] | -14% | +84% | -0.42 [-0.54, -0.30] |  |
|  |  |  | v1 score | -20% [-28%, -13%] | -16% | +84% | -0.33 [-0.45, -0.21] |  |

### Reset 0

| Pair | Circuit | Naive P_L | Method | Kept gain (old weights) | Kept gain (new weights) | Fresh gain | M6 | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| FakeFez, 1 month | A | 5.22e-03 | Decoder-graph | +38% [+33%, +42%] | +36% | +43% | 0.87 [0.74, 1.01] |  |
|  |  |  | Summed error | +12% [+8%, +17%] | +13% | +18% | 0.68 [0.40, 1.07] |  |
|  |  |  | mapomatic | +22% [+17%, +26%] | +23% | +18% | 1.20 [0.86, 1.70] |  |
|  |  |  | v1 score | +49% [+45%, +54%] | +51% | +50% | 0.98 [0.87, 1.10] |  |
| FakeFez, 1 month | B | 8.04e-03 | Decoder-graph | +25% [+22%, +29%] | +25% | +61% | 0.41 [0.35, 0.48] |  |
|  |  |  | Summed error | +45% [+41%, +48%] | +45% | -15% | – |  |
|  |  |  | mapomatic | +45% [+41%, +48%] | +45% | -15% | – |  |
|  |  |  | v1 score | +68% [+65%, +71%] | +68% | +68% | 1.00 [0.93, 1.07] | same choice old and new |
| FakeFez, 1 month | D5 | 7.05e-04 | Decoder-graph | +61% [+51%, +71%] | +60% | +48% | 1.33 [1.01, 1.77] |  |
|  |  |  | Summed error | +46% [+35%, +57%] | +48% | +61% | 0.78 [0.58, 1.04] |  |
|  |  |  | mapomatic | +37% [+27%, +48%] | +34% | +57% | 0.68 [0.46, 0.94] |  |
|  |  |  | v1 score | +47% [+37%, +57%] | +53% | +61% | 0.80 [0.60, 1.05] |  |
| FakeFez, 1 month | D7 | 7.79e-05 | Decoder-graph | +33% [+24%, +44%] | +31% | +59% | 0.66 [0.44, 0.93] |  |
|  |  |  | Summed error | +65% [+56%, +76%] | +65% | +61% | 1.25 [0.98, 1.62] |  |
|  |  |  | mapomatic | +65% [+56%, +76%] | +65% | +56% | 1.36 [1.06, 1.79] |  |
|  |  |  | v1 score | +55% [+45%, +65%] | +55% | +66% | 0.98 [0.76, 1.26] |  |
| FakeHanoi, 14 days | A | 4.58e-03 | Decoder-graph | +0% [+0%, +0%] | +0% | +13% | 0.00 [0.00, 0.00] | old choice is naive |
|  |  |  | Summed error | -1% [-6%, +4%] | -1% | +13% | -0.07 [-0.52, 0.36] |  |
|  |  |  | mapomatic | -1% [-6%, +4%] | -1% | +16% | -0.06 [-0.41, 0.27] |  |
|  |  |  | v1 score | -1% [-6%, +4%] | -1% | -1% | – | same choice old and new |
|  |  |  | Robust rule | +4% [-1%, +9%] | +13% | – | – |  |
| FakeHanoi, 14 days | B | 3.16e-03 | Decoder-graph | +0% [+0%, +0%] | +0% | +0% | – | same choice old and new; old choice is naive |
|  |  |  | Summed error | -102% [-109%, -94%] | -104% | -119% | – |  |
|  |  |  | mapomatic | -110% [-118%, -102%] | -107% | -119% | – |  |
|  |  |  | v1 score | +9% [+3%, +15%] | +1% | +1% | – | same choice old and new |
|  |  |  | Robust rule | +0% [+0%, +0%] | +0% | – | – | old choice is naive |
| FakeHanoi, 14 days | D5 | 9.07e-04 | Decoder-graph | -33% [-45%, -20%] | -7% | +52% | -0.61 [-0.90, -0.37] |  |
|  |  |  | Summed error | +21% [+10%, +32%] | +27% | +9% | – |  |
|  |  |  | mapomatic | +21% [+10%, +32%] | +27% | +27% | 0.76 [0.33, 1.49] | same choice old and new |
|  |  |  | v1 score | +21% [+10%, +32%] | +27% | +27% | 0.76 [0.33, 1.51] | same choice old and new |
|  |  |  | Robust rule | +31% [+22%, +41%] | +33% | – | – |  |
| FakeHanoi, 14 days | D7 | 7.27e-05 | Decoder-graph | -39% [-53%, -25%] | -16% | +36% | -1.13 [-1.82, -0.67] |  |
|  |  |  | Summed error | +10% [-2%, +23%] | +13% | +13% | – | same choice old and new |
|  |  |  | mapomatic | -140% [-159%, -120%] | -85% | +13% | – |  |
|  |  |  | v1 score | -17% [-30%, -4%] | -17% | +17% | – |  |
|  |  |  | Robust rule | -39% [-53%, -25%] | -16% | – | – |  |
| FakeHanoi, 9 months | A | 4.64e-03 | Decoder-graph | +11% [+6%, +16%] | +15% | +15% | 0.71 [0.35, 1.24] | same choice old and new |
|  |  |  | Summed error | +11% [+6%, +16%] | +15% | +15% | 0.71 [0.35, 1.25] | same choice old and new |
|  |  |  | mapomatic | -3% [-8%, +3%] | +2% | +15% | -0.19 [-0.62, 0.18] |  |
|  |  |  | v1 score | +7% [+2%, +13%] | +15% | +2% | – |  |
| FakeHanoi, 9 months | B | 3.08e-03 | Decoder-graph | +0% [+0%, +0%] | +0% | +0% | – | same choice old and new; old choice is naive |
|  |  |  | Summed error | -121% [-129%, -113%] | -117% | -117% | – | same choice old and new |
|  |  |  | mapomatic | -115% [-123%, -107%] | -111% | -117% | – |  |
|  |  |  | v1 score | +0% [-6%, +7%] | -6% | -6% | – | same choice old and new |
| FakeHanoi, 9 months | D5 | 1.08e-03 | Decoder-graph | +29% [+20%, +38%] | +26% | +52% | 0.65 [0.44, 0.91] |  |
|  |  |  | Summed error | -17% [-28%, -6%] | -13% | +6% | – |  |
|  |  |  | mapomatic | +2% [-9%, +13%] | +8% | +33% | 0.07 [-0.33, 0.48] |  |
|  |  |  | v1 score | +11% [+0%, +21%] | +18% | +33% | 0.38 [0.00, 0.84] |  |
| FakeHanoi, 9 months | D7 | 7.96e-05 | Decoder-graph | -58% [-72%, -45%] | -34% | +31% | -2.17 [-3.17, -1.51] |  |
|  |  |  | Summed error | +15% [+4%, +25%] | +23% | -7% | – |  |
|  |  |  | mapomatic | +17% [+9%, +25%] | +17% | -7% | – |  |
|  |  |  | v1 score | -1% [-10%, +8%] | -7% | +23% | -0.05 [-0.55, 0.45] |  |
| FakeTorino, 12 months | A | 2.94e-02 | Decoder-graph | +82% [+80%, +83%] | +82% | +91% | 0.93 [0.91, 0.96] |  |
|  |  |  | Summed error | +32% [+30%, +34%] | +50% | +73% | 0.46 [0.43, 0.49] |  |
|  |  |  | mapomatic | -31% [-34%, -29%] | -3% | +73% | -0.44 [-0.48, -0.41] |  |
|  |  |  | v1 score | -31% [-33%, -28%] | -27% | +90% | -0.35 [-0.38, -0.33] |  |
| FakeTorino, 12 months | B | 2.96e-02 | Decoder-graph | -799% [-803%, -795%] | -46% | +83% | -10.17 [-10.38, -9.97] | uses 2 uncalibrated couplers |
|  |  |  | Summed error | +41% [+39%, +42%] | +52% | +78% | 0.55 [0.52, 0.58] |  |
|  |  |  | mapomatic | -25% [-28%, -23%] | -11% | +70% | -0.38 [-0.42, -0.35] |  |
|  |  |  | v1 score | +52% [+50%, +54%] | +53% | +84% | 0.66 [0.63, 0.68] |  |
| FakeTorino, 12 months | D5 | 3.54e-03 | Decoder-graph | -620% [-632%, -608%] | -25% | +90% | -7.49 [-7.91, -7.11] | uses 2 uncalibrated couplers |
|  |  |  | Summed error | -17% [-23%, -11%] | -4% | +66% | -0.29 [-0.40, -0.18] |  |
|  |  |  | mapomatic | -17% [-23%, -11%] | -4% | +71% | -0.27 [-0.36, -0.17] |  |
|  |  |  | v1 score | -60% [-66%, -53%] | -17% | +73% | -0.90 [-1.02, -0.78] |  |
| FakeTorino, 12 months | D7 | 1.10e-03 | Decoder-graph | -195% [-206%, -182%] | +28% | +97% | -2.59 [-2.90, -2.33] | uses 2 uncalibrated couplers |
|  |  |  | Summed error | +53% [+44%, +61%] | +58% | +81% | 0.84 [0.69, 1.01] |  |
|  |  |  | mapomatic | -49% [-61%, -37%] | -35% | +81% | -0.78 [-1.00, -0.58] |  |
|  |  |  | v1 score | +53% [+44%, +61%] | +58% | +81% | 0.84 [0.69, 1.01] |  |

## 2. FakeTorino's uncalibrated couplers at their older value

Only six rows use a coupler that became uncalibrated. In each one, the decoder-graph score's old choice on FakeTorino after 12 months uses couplers 74-86 and 86-87, whose older gate errors were 0.24% and 0.28%. No row of the other 26 drift cases uses such a coupler. The other rows of these six cases produce the same circuit under both gradings (we checked), and the fresh choices cannot use those couplers, so the fixed-placement rows and every other method's figures stand. At the limit, the rows reproduce the main analysis exactly. Kept gain is the share of the fixed placement's P_L, decoded with old weights as in section 1.

| Setting | Circuit | Kept gain at the limit | Kept gain at the older value | M6 at the older value |
|---|---|---|---|---|
| reset = readout | A | −439% [−442%, −437%] | +72% [+71%, +73%] | 0.81 [0.79, 0.83] |
| reset = readout | D5 | −504% [−511%, −496%] | +53% [+49%, +56%] | 0.69 [0.64, 0.75] |
| reset = readout | D7 | −153% [−162%, −144%] | +81% [+76%, +86%] | 1.14 [1.05, 1.24] |
| reset 0 | B | −799% [−803%, −795%] | +33% [+31%, +35%] | 0.42 [0.40, 0.45] |
| reset 0 | D5 | −620% [−632%, −608%] | +31% [+25%, +36%] | 0.37 [0.30, 0.44] |
| reset 0 | D7 | −195% [−206%, −182%] | +72% [+64%, +80%] | 0.95 [0.83, 1.10] |

On FakeTorino, the decoder-graph score's drift result depends on a value the data does not hold. If the two couplers are as bad as the limit, its old choice is far worse than the fixed placement. If they kept their older error, it keeps a gain of 31% to 81% of the fixed placement's P_L. These snapshots cannot tell us which is real, so the FakeTorino rows of section 1 bound the effect from one side only.

## 3. v2's synthetic drift, re-graded with stale weights

v2 decoded every drifted choice with the true model's weights, so its decoder knew about drift that its placement had not seen. The re-grade simulates v2's own chunks again under the true model, with v2's seeds and shots. The true-weight counts equal v2's stored counts exactly in all six cases, and the run checks this. Each draw's choice is then also decoded with the weights of the model it was chosen on (*stale weights*). *M6 lost* is M6 with true weights minus M6 with stale weights. *Harmful draws* counts the draws whose choice is worse than the fixed placement.

- **Decoder-graph score:** M6 lost lies between −0.23 and +0.09, and every interval covers 0. Harmful draws change by at most 3 of 20. v2's drift result for the score holds with stale weights.
- **Summed error and v2's Qiskit VF2 baseline on FakeJakarta A:** the loss is real (M6 lost 0.53 to 1.09, every interval above 0), and harmful draws rise from 0 to 3 or 4 of 20 at δ = 0.25 and 0.5. Decoding with true weights flattered these baselines, not the score.

| Case | δ | Method | M6, true weights | M6, stale weights | M6 lost | Harmful draws, true / stale weights |
| --- | --- | --- | --- | --- | --- | --- |
| FakeJakarta A | 0.1 | v1 score | 1.02 [1.00, 1.04] | 0.98 [0.91, 1.06] | 0.04 [-0.04, 0.11] | 0 / 0 of 20 |
| FakeJakarta A | 0.1 | Decoder-graph | 1.01 [1.00, 1.04] | 0.99 [0.91, 1.07] | 0.02 [-0.05, 0.10] | 0 / 0 of 20 |
| FakeJakarta A | 0.1 | Summed error | 2.02 [1.23, 3.25] | 1.25 [0.40, 2.50] | 0.78 [0.46, 1.04] | 0 / 0 of 20 |
| FakeJakarta A | 0.1 | Qiskit VF2 (v2) | 1.70 [1.00, 2.79] | 1.17 [0.42, 2.34] | 0.53 [0.16, 0.84] | 0 / 0 of 20 |
| FakeJakarta A | 0.25 | v1 score | 1.03 [1.01, 1.06] | 0.92 [0.82, 1.03] | 0.11 [0.00, 0.21] | 0 / 0 of 20 |
| FakeJakarta A | 0.25 | Decoder-graph | 1.02 [1.00, 1.04] | 0.94 [0.84, 1.05] | 0.07 [-0.04, 0.18] | 0 / 0 of 20 |
| FakeJakarta A | 0.25 | Summed error | 2.53 [1.55, 4.03] | 1.44 [0.50, 2.81] | 1.09 [0.68, 1.56] | 0 / 3 of 20 |
| FakeJakarta A | 0.25 | Qiskit VF2 (v2) | 2.01 [1.18, 3.26] | 1.03 [0.15, 2.22] | 0.98 [0.58, 1.42] | 0 / 3 of 20 |
| FakeJakarta A | 0.5 | v1 score | 0.96 [0.82, 1.06] | 0.79 [0.62, 0.96] | 0.16 [0.03, 0.29] | 0 / 0 of 20 |
| FakeJakarta A | 0.5 | Decoder-graph | 0.93 [0.80, 1.03] | 0.84 [0.68, 0.99] | 0.09 [-0.02, 0.19] | 0 / 0 of 20 |
| FakeJakarta A | 0.5 | Summed error | 3.05 [1.89, 4.75] | 2.01 [0.84, 3.72] | 1.03 [0.42, 1.67] | 0 / 3 of 20 |
| FakeJakarta A | 0.5 | Qiskit VF2 (v2) | 2.76 [1.71, 4.39] | 1.82 [0.73, 3.47] | 0.94 [0.42, 1.49] | 0 / 4 of 20 |
| FakeJakarta B | 0.1 | v1 score | – | – | – | 0 / 0 of 20 |
| FakeJakarta B | 0.1 | Decoder-graph | – | – | – | 0 / 0 of 20 |
| FakeJakarta B | 0.1 | Summed error | – | – | – | 20 / 20 of 20 |
| FakeJakarta B | 0.1 | Qiskit VF2 (v2) | – | – | – | 20 / 20 of 20 |
| FakeJakarta B | 0.25 | v1 score | – | – | – | 0 / 0 of 20 |
| FakeJakarta B | 0.25 | Decoder-graph | – | – | – | 0 / 0 of 20 |
| FakeJakarta B | 0.25 | Summed error | – | – | – | 20 / 20 of 20 |
| FakeJakarta B | 0.25 | Qiskit VF2 (v2) | – | – | – | 20 / 20 of 20 |
| FakeJakarta B | 0.5 | v1 score | – | – | – | 6 / 6 of 20 |
| FakeJakarta B | 0.5 | Decoder-graph | – | – | – | 6 / 6 of 20 |
| FakeJakarta B | 0.5 | Summed error | – | – | – | 20 / 20 of 20 |
| FakeJakarta B | 0.5 | Qiskit VF2 (v2) | – | – | – | 20 / 20 of 20 |
| FakeHanoi A | 0.1 | v1 score | – | – | – | 0 / 0 of 20 |
| FakeHanoi A | 0.1 | Decoder-graph | 0.83 [0.66, 1.00] | 1.06 [0.74, 1.65] | -0.23 [-0.78, 0.06] | 0 / 0 of 20 |
| FakeHanoi A | 0.1 | Summed error | 0.89 [0.72, 1.00] | 1.19 [0.81, 1.86] | -0.29 [-0.96, 0.05] | 0 / 0 of 20 |
| FakeHanoi A | 0.1 | Qiskit VF2 (v2) | 1.00 [0.97, 1.06] | 1.29 [0.94, 1.97] | -0.29 [-0.95, 0.06] | 0 / 0 of 20 |
| FakeHanoi A | 0.25 | v1 score | – | – | – | 0 / 3 of 20 |
| FakeHanoi A | 0.25 | Decoder-graph | 0.65 [0.40, 0.87] | 0.72 [0.37, 1.25] | -0.07 [-0.55, 0.23] | 0 / 1 of 20 |
| FakeHanoi A | 0.25 | Summed error | 0.42 [0.06, 0.69] | 0.48 [0.10, 0.98] | -0.06 [-0.55, 0.25] | 1 / 2 of 20 |
| FakeHanoi A | 0.25 | Qiskit VF2 (v2) | 0.49 [0.12, 0.79] | 0.47 [0.08, 0.96] | 0.02 [-0.44, 0.31] | 1 / 2 of 20 |
| FakeHanoi A | 0.5 | v1 score | – | – | – | 5 / 8 of 20 |
| FakeHanoi A | 0.5 | Decoder-graph | 0.35 [-0.02, 0.65] | 0.29 [-0.09, 0.77] | 0.07 [-0.39, 0.37] | 1 / 4 of 20 |
| FakeHanoi A | 0.5 | Summed error | 0.07 [-0.42, 0.42] | 0.03 [-0.49, 0.60] | 0.04 [-0.43, 0.36] | 3 / 7 of 20 |
| FakeHanoi A | 0.5 | Qiskit VF2 (v2) | 0.11 [-0.39, 0.48] | 0.14 [-0.38, 0.68] | -0.03 [-0.50, 0.28] | 4 / 5 of 20 |
| FakeHanoi B | 0.1 | v1 score | – | – | – | 0 / 0 of 20 |
| FakeHanoi B | 0.1 | Decoder-graph | – | – | – | 0 / 0 of 20 |
| FakeHanoi B | 0.1 | Summed error | – | – | – | 20 / 20 of 20 |
| FakeHanoi B | 0.1 | Qiskit VF2 (v2) | – | – | – | 20 / 20 of 20 |
| FakeHanoi B | 0.25 | v1 score | – | – | – | 0 / 0 of 20 |
| FakeHanoi B | 0.25 | Decoder-graph | – | – | – | 1 / 1 of 20 |
| FakeHanoi B | 0.25 | Summed error | – | – | – | 19 / 19 of 20 |
| FakeHanoi B | 0.25 | Qiskit VF2 (v2) | – | – | – | 19 / 19 of 20 |
| FakeHanoi B | 0.5 | v1 score | – | – | – | 10 / 10 of 20 |
| FakeHanoi B | 0.5 | Decoder-graph | – | – | – | 8 / 8 of 20 |
| FakeHanoi B | 0.5 | Summed error | – | – | – | 19 / 19 of 20 |
| FakeHanoi B | 0.5 | Qiskit VF2 (v2) | – | – | – | 19 / 19 of 20 |
| FakeHanoi D5 | 0.1 | v1 score | 0.99 [0.93, 1.05] | 1.02 [0.81, 1.34] | -0.03 [-0.33, 0.16] | 0 / 0 of 20 |
| FakeHanoi D5 | 0.1 | Decoder-graph | 0.97 [0.91, 1.02] | 0.99 [0.87, 1.15] | -0.02 [-0.17, 0.10] | 0 / 0 of 20 |
| FakeHanoi D5 | 0.1 | Summed error | 1.30 [1.05, 2.06] | 1.35 [0.78, 3.39] | -0.06 [-1.52, 0.35] | 0 / 0 of 20 |
| FakeHanoi D5 | 0.1 | Qiskit VF2 (v2) | 0.57 [0.32, 0.79] | 0.60 [0.42, 0.88] | -0.03 [-0.40, 0.24] | 0 / 0 of 20 |
| FakeHanoi D5 | 0.25 | v1 score | 0.93 [0.80, 1.07] | 0.98 [0.77, 1.29] | -0.05 [-0.33, 0.15] | 0 / 0 of 20 |
| FakeHanoi D5 | 0.25 | Decoder-graph | 0.96 [0.90, 1.02] | 1.00 [0.87, 1.16] | -0.04 [-0.19, 0.08] | 0 / 0 of 20 |
| FakeHanoi D5 | 0.25 | Summed error | 1.98 [1.34, 4.19] | 2.06 [1.22, 5.25] | -0.08 [-1.27, 0.37] | 0 / 0 of 20 |
| FakeHanoi D5 | 0.25 | Qiskit VF2 (v2) | 0.63 [0.33, 0.91] | 0.68 [0.39, 1.08] | -0.05 [-0.40, 0.19] | 2 / 2 of 20 |
| FakeHanoi D5 | 0.5 | v1 score | 0.86 [0.68, 1.04] | 1.03 [0.78, 1.39] | -0.17 [-0.50, 0.07] | 0 / 0 of 20 |
| FakeHanoi D5 | 0.5 | Decoder-graph | 0.75 [0.56, 0.90] | 0.88 [0.67, 1.10] | -0.14 [-0.30, 0.00] | 2 / 1 of 20 |
| FakeHanoi D5 | 0.5 | Summed error | 1.63 [0.80, 3.61] | 1.98 [0.92, 5.27] | -0.35 [-1.95, 0.18] | 2 / 2 of 20 |
| FakeHanoi D5 | 0.5 | Qiskit VF2 (v2) | 0.69 [0.23, 1.16] | 0.90 [0.43, 1.53] | -0.21 [-0.63, 0.06] | 4 / 3 of 20 |
| FakeHanoi D7 | 0.1 | v1 score | – | – | – | 0 / 0 of 20 |
| FakeHanoi D7 | 0.1 | Decoder-graph | 0.95 [0.84, 1.00] | 0.94 [0.66, 1.48] | 0.01 [-0.53, 0.28] | 0 / 0 of 20 |
| FakeHanoi D7 | 0.1 | Summed error | – | – | – | 0 / 0 of 20 |
| FakeHanoi D7 | 0.1 | Qiskit VF2 (v2) | – | – | – | 3 / 3 of 20 |
| FakeHanoi D7 | 0.25 | v1 score | – | – | – | 1 / 1 of 20 |
| FakeHanoi D7 | 0.25 | Decoder-graph | 0.78 [0.57, 0.95] | 0.78 [0.51, 1.30] | -0.00 [-0.53, 0.26] | 0 / 0 of 20 |
| FakeHanoi D7 | 0.25 | Summed error | – | – | – | 6 / 5 of 20 |
| FakeHanoi D7 | 0.25 | Qiskit VF2 (v2) | – | – | – | 8 / 9 of 20 |
| FakeHanoi D7 | 0.5 | v1 score | – | – | – | 4 / 4 of 20 |
| FakeHanoi D7 | 0.5 | Decoder-graph | 0.17 [-0.44, 0.58] | 0.24 [-0.26, 0.77] | -0.08 [-0.61, 0.23] | 3 / 4 of 20 |
| FakeHanoi D7 | 0.5 | Summed error | – | – | – | 8 / 7 of 20 |
| FakeHanoi D7 | 0.5 | Qiskit VF2 (v2) | – | – | – | 9 / 8 of 20 |

## Reproducibility

Run from `benchmarks/`. Both commands rebuild their `analysis.json` byte for byte from the committed run files.

```bash
python -m nac_bench.run_drift configs_v3/drift.json --analyse
python -m nac_bench.regrade_v2_drift configs_v3/regrade_v2_drift.json --analyse
```

The uncalibrated-coupler grading is `nac_bench/drift_uncalibrated.py`, with its data in `results_v3/drift/uncalibrated/regrade.json`.
