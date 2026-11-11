# noise-aware-compilation

A Python package built on [Nove](https://github.com/KUQCI/nove) (commit `485b413`) that
chooses a SWAP-free placement of a QEC circuit's qubits on a device from that device's
calibrated noise, and measures in simulation whether that choice lowers the decoded logical
error rate compared with a fixed placement and with every other valid placement.

<!-- v3-banner:start -->
> **v3 re-tests these results.** Read v1 and v2 with v3's re-analyses beside them:
> [RESET.md](results_v3/reset/RESET.md), [REANALYSIS.md](results_v3/reanalysis/REANALYSIS.md)
> and [DRIFT.md](results_v3/drift/DRIFT.md); the plan is [SPEC_V3.md](SPEC_V3.md). On
> FakeAlgiers, v2's held-out device, with reset error = readout error, the decoder-graph score
> beats the naive (fixed) placement in 4 of 4 cases and beats lowest summed error, Qiskit at
> level 1 (level 2, the actual default, picks the same placement in every v2 case) and
> mapomatic in 1 of 4 after Holm; the rest do not hold. Under real calibration drift a stale
> choice can lose its gain: on FakeHanoi d=7 the decoder-graph score's old choice became worse
> than naive, and at reset = readout, on FakeFez and FakeTorino, summed error kept more of its
> gain than the score in three of four circuits each. At reset = readout the v1 score lost to
> the naive placement in three FakeHanoiV2 d=3 circuits on v1's seeds (one, 5 rounds, had been
> a gain); on fresh seeds one loss (3 rounds) replicates (banner in [REPORT.md](REPORT.md)).
<!-- v3-banner:end -->

- Specification: [SPEC.md](SPEC.md) (binding; revised three times, each revision recorded at
  its top), with [SPEC_V2.md](SPEC_V2.md) and [SPEC_V3.md](SPEC_V3.md) for later versions.
- Open choices and reasons: [DECISIONS.md](DECISIONS.md).
- Results: [REPORT.md](REPORT.md), raw results in `results/runs/`, figures in `results/figures/`.

All results hold under the converted noise model only; there are no hardware claims.

## Install

Python ≥ 3.10. Nove is built from source with maturin, so Rust stable and a C/C++ toolchain
are needed.

```bash
python -m pip install --upgrade pip maturin
python -m pip install -e ".[report]"      # runtime + matplotlib for the report
python -m pip install -e ".[dev]"         # adds pytest, ruff, mypy, networkx, scipy, qiskit-ibm-runtime
```

Qiskit is never a runtime dependency. It is only in the `dev` extra, through
qiskit-ibm-runtime, which `scripts/fetch_device_files.py` uses to copy the fake-backend files,
and in the `bench` extra (Qiskit 2.5.2 and mapomatic) for the v2 and v3 baselines.

## Place your own circuit (v2 command line)

`nac` converts a device's calibration files and places a Stim circuit on it. It needs no
Qiskit, no decoder and no simulation. Worked example, from a fresh install to a placed
circuit, using the circuit and device files shipped with the package (a distance-3
bit-flip repetition code and FakeJakartaV2's IBM file pair):

<!-- worked-example:start -->
```bash
python -m pip install "noise_aware_compilation @ git+https://github.com/KUQCI/noise-aware-compilation@main"
nac example --out example
nac convert --conf example/conf_jakarta.json --props example/props_jakarta.json --out my_device
nac place --circuit example/repetition_d3_bitflip.stim --device my_device --out placed
cat placed/mapping.json
```
<!-- worked-example:end -->

`placed/mapping.json` holds the chosen mapping (circuit qubit → physical qubit), its score,
the scorer, every valid candidate with its score, the SHA-256 of every input file and the
package version; `placed/placed_circuit.stim` is the relabelled circuit. To skip the
conversion step, pass the IBM files directly (`--conf FILE --props FILE` instead of
`--device`), or a shipped device by name (`algiers`, `fez`, `hanoi`, `jakarta`, `marrakesh`
or `miami`; for example `--device hanoi`). `--scorer` picks the selection score:
`decoder-graph` (the default, chosen by the held-out comparison in REPORT_V2.md section 4,
which rests on its d=7 case; v3 re-tests it) or `v1`. Run `nac --help` or
`nac COMMAND --help` for every option. Invalid input exits with code 1 and a one-line
message; `nac --debug ...` shows the traceback.

The same two operations are in Python, with the same inputs and outputs:

```python
from noise_aware_compilation.api import convert_device, place_circuit

convert_device("example/conf_jakarta.json", "example/props_jakarta.json", "my_device")
placed = place_circuit("example/repetition_d3_bitflip.stim", "placed", device="my_device")
placed.mapping  # {0: 6, 1: 5, 2: 3, 3: 1, 4: 2}
```

## Reproduce every result (R-D7)

One command, from the repository root, regenerates every results file, every figure and
REPORT.md from `configs/benchmark.json` and its seeds:

```bash
python -m noise_aware_compilation.benchmark configs/benchmark.json && python -m noise_aware_compilation.report
```

Counts are bit-identical for the same config and seeds (T-R1). The runner refuses to run with
uncommitted changes to code, data or config (pass `--allow-dirty` for a scratch run), so
every results file names the commit that produced it. `--workers N` sets the number
of processes (default: all CPUs); `--only CASE_ID ...` runs selected cases.

## Reproduce the v2 results (SPEC_V2.md)

The v2 benchmark (drift robustness, the lowest-summed-error and Qiskit layout baselines, and
the held-out FakeAlgiers comparison of the two scores) needs the `bench` extra
(`pip install -e ".[bench]"`, which pins Qiskit 2.5.2). From the repository root:

```bash
python -m nac_bench.run_v2 configs_v2/benchmark_v2.json && python -m nac_bench.report_v2
```

This writes `results_v2/runs/*.json`, `results_v2/figures/`, `results_v2/summary.json` and
REPORT_V2.md. v1's `configs/`, `results/` and REPORT.md are not touched. The runner has the
same `--workers`, `--only` and `--allow-dirty` options as v1's. `tests/test_v2_results.py`
checks the committed v2 results and REPORT_V2.md.

## Use

```python
from noise_aware_compilation import load_device, place, relabel_circuit, repetition_code

stored = load_device("hanoi")  # Nove DeviceModel + CalibrationSnapshot
circuit = repetition_code(distance=5, rounds=3)  # noiseless nove.Circuit
result = place(circuit, stored.device, stored.noise)
result.mapping_dict  # circuit qubit -> physical qubit
result.score  # selection score (lower is better)
result.candidates  # every valid SWAP-free mapping with its score
mapped = relabel_circuit(circuit, result.mapping)  # runs in Nove with that device and noise
```

`place` accepts any Nove `DeviceModel` and `HardwareNoiseModel`. `convert_fake_backend`
converts `conf_*.json` / `props_*.json` files into a Nove `CalibrationSnapshot` and
`DeviceModel` by the SPEC.md conversion rules.

## Checks (R-D10)

```bash
pytest            # includes ruff check, ruff format --check and mypy --strict (T-I6)
ruff check . && ruff format --check . && mypy --strict
```

`tests/test_results.py` checks the committed results files and REPORT.md against the test
specification.

## Data maintenance (dev only)

```bash
python scripts/fetch_device_files.py           # copy device files from qiskit-ibm-runtime 0.50.0
python scripts/build_device_snapshots.py       # convert them into the package's stored snapshots
python scripts/write_fixed_baselines.py configs/benchmark.json   # R-B1 fixed placements
bash scripts/check_no_qiskit.sh                 # T-I8: fresh venv without Qiskit runs a case
```
