# Results

**Short version:** in simulation, the decoder-graph score picks placements with far fewer
logical errors than an arbitrary placement on every device tested. On the square lattice it
also clearly beats the layout tools people use today (Qiskit's default layout, mapomatic and
lowest summed error). On heavy-hex devices its lead over those tools is small and depends on
modelling details.

Every number here comes from simulating the converted calibration of an IBM device with Nove
and decoding with PyMatching. Nothing was run on hardware. "Fewer logical errors" means a
lower logical error rate per shot (P_L), with 95% confidence intervals and Holm's correction
for multiple comparisons.

## The final test (v3)

**Design.** Before any held-out calibration file was opened, we froze the score, the code,
every random seed and the rules for what counts as a win. Then we converted three IBM devices
nobody had looked at and ran every comparison. Those devices were FakeBoston and
FakePittsburgh (heavy-hex, 156 qubits) and FakeBerlin (square lattice). All three use CZ
gates, which the method had never been developed on. Each case was a repetition code
(distance 3 bit-flip and phase-flip, distance 5 and 7 bit-flip, 3 rounds). Reset errors were
set equal to readout errors.

**Against a fixed placement** (the first valid placement in index order, which uses no noise
information):

| Device | Cases better | Reduction in logical errors |
| --- | --- | --- |
| FakeBoston | 4 of 4 | 79–93% |
| FakePittsburgh | 4 of 4 | 79–93% |
| FakeBerlin | 4 of 4 | 45–68% |

The fixed placement happened to be a poor draw on the heavy-hex devices (81st–92nd percentile)
and a good one on FakeBerlin (1st–7th percentile). The decoder-graph's choice was in the best
1% of all placements in every case.

**Against the tools people use** (better / worse after correction, of 8 heavy-hex or 4
square-lattice cases):

| Against | Heavy-hex | Square lattice |
| --- | --- | --- |
| Lowest summed error | 5 better, 1 worse | 4 better, 0 worse (28–50%) |
| Qiskit's default layout (level 2) | 5 better, 1 worse | 4 better, 0 worse (28–41%) |
| mapomatic | 5 better, 1 worse | 4 better, 0 worse (28–53%) |

On heavy-hex the wins are at distance 3 and 5. At distance 7 it ties or loses.

**How robust is it?** We re-checked the same choices 15 ways: different gate lengths, more
dephasing, random noise on every rate, readout errors by direction, more rounds, and a
different decoder setting.

- The wins over the fixed placement held in every check.
- FakeBerlin's wins held in every check but one comparison in one random draw.
- The heavy-hex wins over the three tools mostly did not hold. With readout errors modelled
  by direction (a Z-basis repetition code mostly meets the 0→1 error, about half the average),
  the score's heavy-hex choices were worse than summed error and Qiskit's default in 5 of 6
  cases.

**Surface code.** FakeBerlin has no distance-5 surface-code placement that avoids its
uncalibrated couplers, so there is no confirmatory surface-code result. On a synthetic device
(FakeNighthawk), the surface-code choice beat the fixed placement and tied the other tools.

**Calibration drift.** We chose placements on an older IBM calibration and graded them on a
newer one, as someone who had not recalibrated would.

- After a month on FakeFez, every method's old choice still beat the fixed placement.
- After 14 days on FakeHanoi, the decoder-graph's distance-7 choice became 127% worse than
  the fixed placement.
- Summed error kept more of its gain than the decoder-graph score in 3 of 4 FakeFez circuits.
- Choosing again on the new calibration restored the gain.

Full v3 report: [reports/REPORT_V3.md](reports/REPORT_V3.md).

## How we got here

**v1: does placement matter at all?** Two devices (FakeJakarta, 7 qubits; FakeHanoi, 27
qubits) and a simple score: the expected number of detectable faults. It beat the fixed
placement by 23–33% on FakeJakarta and 19–36% on FakeHanoi at distance 5 and 7. An audit then
found the first FakeHanoi numbers were inflated by two couplers IBM had marked uncalibrated.
The converter now removes such couplers. A later check with realistic reset errors turned
some of v1's small FakeHanoi wins into losses.
[Report](reports/REPORT.md).

**v2: a better score and real competitors.** We added the decoder-graph score, Qiskit's
layout as a baseline, synthetic calibration drift, and a held-out device (FakeAlgiers). The
decoder-graph score beat the fixed placement in all 4 FakeAlgiers cases and was made the
default. That decision rested mostly on one distance-7 case. In
that case both methods had chosen the same qubits, and the gain came from running them in the
mirror order. [Report](reports/REPORT_V2.md).

**v3: new hardware, honest settings.** v3 used CZ devices, square-lattice chips, realistic
reset errors, real calibration drift, surface codes, stronger baselines (Qiskit's actual
default and mapomatic) and a frozen, pre-registered test (above). It also re-analysed v1 and
v2 with corrected statistics:
[re-analysis](../benchmarks/results_v3/reanalysis/REANALYSIS.md),
[reset study](../benchmarks/results_v3/reset/RESET.md),
[drift](../benchmarks/results_v3/drift/DRIFT.md).

## Limitations

- **Simulation only.** Choices are graded under the same model that made them. Real chips
  have crosstalk, leakage, coherent errors and readout asymmetry that the model omits or
  simplifies.
- **One calibration per device.** Another day's calibration could change which placement
  wins.
- **Model choices matter.** Idle noise has no dynamical decoupling, every round resets its
  ancillas, and gate durations are averaged per device.
- **Few devices.** The final claims rest on three devices.

How the tests were designed and frozen: [methodology/](methodology/README.md).
