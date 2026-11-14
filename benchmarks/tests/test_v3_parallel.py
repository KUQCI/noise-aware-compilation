"""Selection work split across processes gives the one-process result (SPEC_V3.md R3-C4)."""

from __future__ import annotations

import warnings
from typing import Any

from qiskit import QuantumCircuit

from nac_bench.baselines_v3 import (
    SnapshotBackend,
    mapomatic_choice,
    mapomatic_cost_chunk,
    native_target,
)
from nac_bench.qiskit_layout import qiskit_circuit
from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.devices import load_device
from noise_aware_compilation.placement import enumerate_placements, interaction_edges


def test_mapomatic_in_chunks_equals_one_call() -> None:
    stored = load_device("fez")
    circuit = repetition_code(5, 3, "Z", two_qubit_gate="CZ")
    qc = qiskit_circuit(circuit)
    edges = sorted(interaction_edges(circuit))
    allowed = set(enumerate_placements(circuit, stored.device))
    backend = SnapshotBackend(
        str(stored.snapshot.device_id), native_target(stored.snapshot, stored.device, "readout")
    )

    def in_process(small: QuantumCircuit, chunks: list[list[list[int]]]) -> list[Any]:
        return [mapomatic_cost_chunk(("fez", "readout", small, c)) for c in chunks]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        one = mapomatic_choice(qc, backend, 20261021, edges, allowed)
        split = mapomatic_choice(qc, backend, 20261021, edges, allowed, in_process, 97)
    assert one.num_layouts > 97 * 3
    assert split.mapping == one.mapping and split.cost == one.cost
    assert split.tied_best == one.tied_best
    assert list(split.costs.items()) == list(one.costs.items())
