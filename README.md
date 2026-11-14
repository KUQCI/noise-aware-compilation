# noise-aware-compilation

**Noise-aware qubit placement for quantum error correction, as an extension to
[Nove](https://github.com/KUQCI/nove).**

Before an error-correction circuit can run on a real chip, each of its qubits has to be
assigned to a physical qubit. Calibration quality varies a lot across a chip, so that choice
changes how often the code fails. This package makes the choice for you: it reads a
device's published calibration data, finds every placement of your circuit that needs no
SWAP gates, and picks the one predicted to fail least.

- **Input:** a [Stim](https://github.com/quantumlib/Stim) circuit and an IBM device
  (calibration files, or one of the 12 devices shipped with the package).
- **Output:** the chosen mapping (circuit qubit → physical qubit), its score, every other
  valid placement with its score, and the relabelled circuit, ready to run in Nove.
- **No simulation needed to choose.** The score is computed from the noise model alone, in
  seconds for typical repetition codes.
- Supports heavy-hex (CX) and square-lattice (CZ) IBM devices, repetition codes and the
  rotated surface code.

## Install

Python 3.10 or later. Nove is built from source, so a Rust toolchain and a C/C++ compiler
are needed.

```bash
python -m pip install "noise_aware_compilation @ git+https://github.com/KUQCI/noise-aware-compilation@main"
```

## Quick start

From a fresh install to a placed circuit, using the example files shipped with the package
(a distance-3 repetition code and IBM's 7-qubit Jakarta device):

<!-- worked-example:start -->
```bash
python -m pip install "noise_aware_compilation @ git+https://github.com/KUQCI/noise-aware-compilation@main"
nac example --out example
nac convert --conf example/conf_jakarta.json --props example/props_jakarta.json --out my_device
nac place --circuit example/repetition_d3_bitflip.stim --device my_device --out placed
cat placed/mapping.json
```
<!-- worked-example:end -->

`placed/mapping.json` holds the chosen mapping, its score, every valid candidate with its
score and the hashes of the input files; `placed/placed_circuit.stim` is the relabelled
circuit. Useful options:

- `--device hanoi` uses a shipped device instead of converting files. Shipped devices:
  aachen, algiers, berlin, boston, fez, hanoi, jakarta, kingston, marrakesh, miami,
  nighthawk, pittsburgh.
- `--conf FILE --props FILE` places directly from IBM's calibration files.
- `--scorer decoder-graph` (default) or `--scorer v1` picks the selection score.
- `nac COMMAND --help` lists every option.

The same in Python:

```python
from noise_aware_compilation.api import convert_device, place_circuit

convert_device("example/conf_jakarta.json", "example/props_jakarta.json", "my_device")
placed = place_circuit("example/repetition_d3_bitflip.stim", "placed", device="my_device")
placed.mapping  # {0: 6, 1: 5, 2: 3, 3: 1, 4: 2}
```

Or with Nove objects directly:

```python
from noise_aware_compilation import load_device, place, relabel_circuit, repetition_code
from noise_aware_compilation.decoder_graph import DECODER_GRAPH_SCORER

stored = load_device("hanoi")  # Nove DeviceModel + noise model
circuit = repetition_code(distance=5, rounds=3)  # noiseless Nove circuit
result = place(circuit, stored.device, stored.noise, scorer=DECODER_GRAPH_SCORER)
result.mapping_dict  # circuit qubit -> physical qubit
mapped = relabel_circuit(circuit, result.mapping)  # runs in Nove on that device
```

## How it works

1. **Convert the calibration.** IBM's published gate errors, T1/T2 times and readout errors
   become a Nove noise model (Pauli channels per gate, idle noise per qubit, readout flips).
   Couplers or qubits IBM marks as uncalibrated are removed.
2. **Enumerate placements.** Every one-to-one assignment that puts each two-qubit gate on a
   physical coupler, so no SWAPs are needed.
3. **Score each placement.** The default *decoder-graph score* builds the circuit's error
   graph, the same graph the decoder uses, and estimates the probability of the shortest
   undetectable error chains. Lower is better. A faster *scaled score* handles surface codes.
4. **Pick the lowest score.** Ties go to a fixed, documented order, so results are
   reproducible.

Details, formulas and every conversion rule: [docs/how-it-works.md](docs/how-it-works.md).

## Does it help?

We tested this in simulation. Each placement was simulated with millions of shots under the
converted noise model and decoded with PyMatching. The final test used IBM devices whose
calibration was converted only after the method, code and statistics were frozen.

| Device layout | vs. a fixed (first-valid) placement | vs. Qiskit's default layout, mapomatic and lowest summed error |
| --- | --- | --- |
| Square lattice (FakeBerlin) | Better in 4 of 4 cases, 45–68% fewer logical errors | Better in all 12 comparisons, 28–53% fewer |
| Heavy-hex (FakeBoston, FakePittsburgh) | Better in 8 of 8 cases, 79–93% fewer | Mixed: better in 5 of 8, worse in 1, against each method |

**What this does and doesn't show:**

- These are **simulation results**, graded under the same noise model the method uses to
  choose. They show the method makes good use of the calibration data. They do not show the
  same gain on real hardware.
- The heavy-hex advantage over Qiskit and the others is fragile. It shrinks or reverses when
  readout errors are modelled by direction, and at code distance 7.
- Calibration drifts. A choice made on a weeks-old calibration can lose its advantage.
  Re-running placement on fresh calibration data restores it.

Full summary, including what earlier versions found and what changed:
[docs/results.md](docs/results.md).

## Repository layout

```
src/noise_aware_compilation/   the package (the Nove extension)
tests/                         unit tests for the package
docs/                          how it works, results summary, full reports, methodology
benchmarks/                    benchmark code, configs, raw results and their tests
```

## Tests

```bash
python -m pip install -e ".[dev,bench]"
pytest                    # package tests and benchmark checks, plus ruff and mypy --strict
pytest tests              # package tests only
```

To reproduce the benchmark results, see [benchmarks/README.md](benchmarks/README.md).

## Citation

If you use this package, please cite it using [CITATION.cff](CITATION.cff).
