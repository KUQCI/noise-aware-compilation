# Changelog

## v3 (November 2026), package version 0.2.0

- Support for CZ devices (Heron r2/r3 heavy-hex and the square-lattice Nighthawk family).
  12 converted devices ship with the package.
- Rotated surface code in Z memory, and the *scaled score* for it (k-shortest error chains).
- Local search (`fast_search`) for devices and codes too large to enumerate.
- Final, pre-registered test on three held-out devices with reset error = readout error, real
  calibration drift, and Qiskit's default layout and mapomatic as baselines.
  See [docs/results.md](docs/results.md).
- The repository was reorganised around the package. The research record as it was run is
  at tag `v3.0-results`.

## v2 (October 2026)

- The *decoder-graph score*, made the default.
- `nac` command-line tool and `noise_aware_compilation.api`.
- Qiskit layout baseline, synthetic drift, first held-out device (FakeAlgiers).

## v1 (October 2026)

- IBM calibration converter, including removal of couplers IBM marks as uncalibrated.
- SWAP-free placement enumeration, the v1 score (expected number of detectable faults) and
  repetition codes.
- First benchmark, on FakeJakarta and FakeHanoi.
