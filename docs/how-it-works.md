# How it works

The package does three things: it turns IBM calibration data into a Nove noise model, it lists
every SWAP-free placement of a circuit, and it scores each placement from the noise model
alone. Code: `src/noise_aware_compilation/`.

## 1. From calibration data to a noise model (`conversion.py`)

IBM publishes each device's calibration as a `conf_*.json` / `props_*.json` file pair (the
same files Qiskit's fake backends use). The converter reads them as plain JSON, so Qiskit is
not needed, and builds a Nove `CalibrationSnapshot` and `DeviceModel`:

| Nove quantity | From | Rule |
| --- | --- | --- |
| Connectivity | `coupling_map` | Every listed pair, minus uncalibrated ones (below) |
| Two-qubit gate noise (CX or CZ), each direction | gate error *e* | 15 two-qubit Pauli terms, each 1.25·*e*/15 (a depolarizing channel with that average gate error) |
| Single-qubit (H) noise | `sx` error *e* | p_x = p_y = p_z = 1.5·*e*/3 |
| Idle noise per qubit | T1, T2 | X and Y rate 1/(4·T1), Z rate 1/(2·T2) − 1/(4·T1), clamped at 0 |
| Readout flip per qubit | `readout_error` | Used as given (Nove's readout error is symmetric) |
| Reset flip per qubit | not published | 0, or equal to the readout error (an option used in the benchmarks) |
| Gate, measurement and reset durations | gate and readout lengths | Device mean per gate type (Nove sets durations per gate name) |

**Uncalibrated parts are removed.** IBM reports an error of 1.0 for a coupler or qubit whose
calibration is stale. No Pauli channel has a two-qubit error above 0.8 or a single-qubit error
above 2/3, so the converter drops such couplers in both directions, and drops every coupler
of such a qubit. Every removal is recorded in the device's metadata.

The converted snapshots of 12 devices ship with the package (`device_data/`), each with its
SHA-256 hash.

## 2. Listing placements (`placement.py`)

A placement assigns each circuit qubit to a different physical qubit, such that every
two-qubit gate lands on a physical coupler. No SWAP gates are ever added. The package lists
every such placement by depth-first search, in lexicographic order. If none exists it raises
`NoValidPlacementError` and does not fall back to anything.

For large devices and codes, `fast_search.py` offers a local search that scores a pre-filtered
subset instead of every placement.

## 3. Scoring placements

Each placement's circuit is relabelled onto its physical qubits, and Nove adds the noise
model. Stim then derives the *detector error model*: every way a fault can flip the circuit's
detectors and logical observable, with its probability. All scores are computed from this
model. None of them samples or simulates the circuit.

**Decoder-graph score** (`decoder_graph.py`, the default). The decoder (minimum-weight
matching) sees the detector error model as a graph. A logical failure happens when the errors
along a chain from boundary to boundary outweigh the alternative correction. For every
shortest such chain, the score computes exactly the probability that the decoder fails on it,
and sums them. It estimates the placement's logical error rate under a matching decoder.

**Scaled score** (`scaled_score.py`). The same idea for larger codes. It takes the *k* most
likely chains (k-shortest paths) instead of all shortest ones, and uses a dynamic program
instead of exact enumeration. It is used for surface codes, where it also chooses among
stabilizer gate orders.

**v1 score** (`placement.py`). The expected number of detectable faults per shot: the sum of
all probabilities in the detector error model. It is simpler, and kept for comparison.

**Baselines** (`baselines.py`). Lowest summed error (gate, readout and reset errors only) and
an idle-aware summed error. These are used for comparison, not selection.

Ties are broken by a fixed rule: scores are rounded to 12 significant digits, and the first
placement in lexicographic order wins.

## 4. Circuits (`circuits.py`, `surface_code.py`)

- **Repetition codes.** Any distance and number of rounds, bit-flip (Z) or phase-flip (X).
  They are built natively from CX, or from CZ and H for CZ devices.
- **Rotated surface code** in Z memory, with the hook-safe stabilizer gate orders.

Any other Stim circuit made of gates the noise model covers can be placed too, through
`nac place` or `place_circuit`.

## 5. How results were measured (benchmarks only)

The benchmarks in `benchmarks/` simulate every placement with Nove's Stim backend. Each
placement gets its own random seeds, millions of shots, and a PyMatching decoder built from
that placement's own detector error model. They compare the chosen placement with other
methods, using 95% intervals for the difference and Holm's correction across each family of
comparisons. See [results.md](results.md).
