"""Noise-aware baselines that need no Qiskit (v2 R-B4).

*Lowest summed error* (R-B4): the valid placement whose placed circuit has the
lowest sum, over every gate, measurement and reset operation, of that
operation's total error probability in the Nove noise model it is given. Idle
noise is excluded. A gate's total error probability is the sum of its Pauli
channel's probabilities; a measurement's is the readout flip probability and a
reset's the reset flip probability. Sums use :func:`math.fsum`, so the value is
the correctly rounded sum and does not depend on operation order. Ties go to
the lexicographically smallest mapping. Like every noise-aware method, it
draws only from the candidate set of R-P1 (R-B6).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import nove
import stim

from noise_aware_compilation.placement import (
    Candidate,
    Placement,
    PlacementResult,
    _iter_flat,
    enumerate_placements,
)

SUMMED_ERROR_METHOD = (
    "Sum over every gate, measurement and reset operation of the placed circuit of its total "
    "error probability (Pauli channel total, readout flip or reset flip); idle noise excluded."
)
SUMMED_ERROR_TIE_BREAK = "Exact comparison; ties go to the lexicographically smallest mapping."


def operation_error_terms(
    circuit: nove.Circuit, noise: nove.HardwareNoiseModel, mapping: Sequence[int]
) -> list[float]:
    """Total error probability of each operation of the placed circuit, in circuit order."""

    terms: list[float] = []
    for instruction in _iter_flat(circuit.to_stim()):
        name = instruction.name
        try:
            data = stim.gate_data(name)
        except IndexError:
            continue
        qubits = [mapping[t.value] for t in instruction.targets_copy() if t.is_qubit_target]
        if data.is_reset and not data.produces_measurements:
            terms.extend(noise.reset_probability_for(q) or 0.0 for q in qubits)
        elif data.produces_measurements:
            terms.extend(noise.readout_probability_for(q) or 0.0 for q in qubits)
        elif data.is_unitary:
            arity = 2 if data.is_two_qubit_gate else 1
            for i in range(0, len(qubits), arity):
                key = nove.OperationKey(name, tuple(qubits[i : i + arity]))
                channel = noise.gate_channel_for(key)
                terms.append(math.fsum(channel.probabilities) if channel is not None else 0.0)
    return terms


def summed_error(
    circuit: nove.Circuit, noise: nove.HardwareNoiseModel, mapping: Sequence[int]
) -> float:
    """R-B4 score of one placement (lower is better)."""

    return math.fsum(operation_error_terms(circuit, noise, mapping))


def lowest_summed_error(
    circuit: nove.Circuit, device: nove.DeviceModel, noise: nove.HardwareNoiseModel
) -> PlacementResult:
    """R-B4: the valid placement with the lowest summed operation error."""

    candidates = tuple(
        Candidate(mapping=m, score=summed_error(circuit, noise, m))
        for m in enumerate_placements(circuit, device)
    )
    best = min(candidates, key=lambda c: (c.score, c.mapping))
    return PlacementResult(
        mapping=best.mapping,
        score=best.score,
        candidates=candidates,
        tie_break_rule=SUMMED_ERROR_TIE_BREAK,
        score_method=SUMMED_ERROR_METHOD,
        scorer="lowest-summed-error",
    )


__all__ = [
    "SUMMED_ERROR_METHOD",
    "Placement",
    "lowest_summed_error",
    "operation_error_terms",
    "summed_error",
]
