# Drift with uncalibrated couplers at their older value

Exploratory, SPEC_V3.md revision 6. Development devices only. Code: `nac_bench/drift_uncalibrated.py`; data: `regrade.json`; test: `tests/test_v3_drift_uncalibrated.py`.

The drift runs grade every old choice under the newer snapshot. A coupler that is valid in the older snapshot and uncalibrated (error 1.0) in the newer one has no usable newer value, so the drift runs simulate it at the converter's limit, gate error 0.8 (P2-22). An uncalibrated coupler is stale in IBM's data, so its true error is unknown in both directions, and the limit is only one end of the range. This run grades the same placements at the other end, with each such coupler at its older-snapshot value and everything else unchanged.

Only six rows use such a coupler. In each one the decoder-graph score's old choice on FakeTorino, 12 months, uses couplers 74-86 and 86-87, whose older gate errors are 0.24% and 0.28%. No row of the other 26 drift cases uses such a coupler, the other rows of these six cases lower to the same circuit under both gradings (checked), and the fresh choices cannot use those couplers. The naive rows and every other method's figures therefore stand. At the limit the rows reproduce `analysis.json` exactly. The kept gain is the share of naive's P<sub>L</sub>, decoded with old weights as in DRIFT.md.

| Setting | Circuit | Kept gain at the limit | Kept gain at the older value | M6 at the older value |
|---|---|---|---|---|
| reset = readout | A | −439% [−442%, −437%] | +72% [+71%, +73%] | 0.81 [0.79, 0.83] |
| reset = readout | D5 | −504% [−511%, −496%] | +53% [+49%, +56%] | 0.69 [0.64, 0.75] |
| reset = readout | D7 | −153% [−162%, −144%] | +81% [+76%, +86%] | 1.14 [1.05, 1.24] |
| reset 0 | B | −799% [−803%, −795%] | +33% [+31%, +35%] | 0.42 [0.40, 0.45] |
| reset 0 | D5 | −620% [−632%, −608%] | +31% [+25%, +36%] | 0.37 [0.30, 0.44] |
| reset 0 | D7 | −195% [−206%, −182%] | +72% [+64%, +80%] | 0.95 [0.83, 1.10] |

On FakeTorino the decoder-graph's drift result depends on a value the data does not hold. If the two couplers are as bad as the limit, its old choice is far worse than naive. If they kept their older error, it keeps a gain of 31% to 81% of naive's P<sub>L</sub>. Which end is real cannot be known from these snapshots, so the FakeTorino rows of DRIFT.md bound the effect from one side only.
