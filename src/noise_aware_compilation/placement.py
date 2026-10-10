"""SWAP-free noise-aware placement (R-D1, R-D2, R-P1 to R-P6).

A *mapping* is a tuple ``m`` with ``m[c]`` the physical qubit assigned to
circuit qubit ``c``. A mapping is *valid* when it is one-to-one and every
two-qubit gate pair of the circuit lands on a device connection (no SWAPs).

Selection scores each valid mapping from the circuit, the device and the
noise model only (R-P4): the relabelled circuit is lowered with Nove's
calibrated noise, Stim derives the (approximate, K1) detector error model,
and the score is the total probability of its fault mechanisms - the expected
number of detector-visible faults per shot. Lower is better. No sampler is
constructed and no logical error rate is estimated (T-C8).

Tie-break (R-P5, DECISIONS.md D2): scores are compared after rounding to 12
significant digits; among equal scores the lexicographically smallest mapping
wins.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

import nove
import stim

Placement = tuple[int, ...]

SCORE_SIGNIFICANT_DIGITS = 12
TIE_BREAK_RULE = (
    "Scores are rounded to 12 significant digits; the lowest rounded score wins and ties "
    "go to the lexicographically smallest mapping (circuit qubit order)."
)
SCORE_METHOD = (
    "Total probability of fault mechanisms in Stim's approximate detector error model "
    "(approximate_disjoint_errors=True) of the relabelled circuit lowered with the Nove "
    "hardware noise model: the expected number of detector-visible faults per shot."
)


class NoValidPlacementError(ValueError):
    """Raised when no SWAP-free placement of the circuit exists on the device."""


@dataclass(frozen=True)
class Candidate:
    mapping: Placement
    score: float

    def as_dict(self) -> dict[int, int]:
        return dict(enumerate(self.mapping))


@dataclass(frozen=True)
class PlacementResult:
    """Chosen mapping, its score and every valid candidate with its score (R-D1)."""

    mapping: Placement
    score: float
    candidates: tuple[Candidate, ...]
    tie_break_rule: str = TIE_BREAK_RULE
    score_method: str = SCORE_METHOD

    @property
    def mapping_dict(self) -> dict[int, int]:
        return dict(enumerate(self.mapping))


def _iter_flat(circuit: stim.Circuit) -> Iterator[stim.CircuitInstruction]:
    for item in circuit:
        if isinstance(item, stim.CircuitRepeatBlock):
            body = item.body_copy()
            for _ in range(item.repeat_count):
                yield from _iter_flat(body)
        else:
            yield item


def interaction_edges(circuit: nove.Circuit) -> frozenset[tuple[int, int]]:
    """Undirected circuit-qubit pairs acted on by a two-qubit gate."""

    edges: set[tuple[int, int]] = set()
    for instruction in _iter_flat(circuit.to_stim()):
        try:
            data = stim.gate_data(instruction.name)
        except IndexError:
            continue
        if not data.is_two_qubit_gate:
            continue
        for group in instruction.target_groups():
            qubits = [t.qubit_value for t in group if t.qubit_value is not None]
            if len(qubits) == 2 and qubits[0] != qubits[1]:
                a, b = qubits
                edges.add((min(a, b), max(a, b)))
    return frozenset(edges)


def device_qubits(device: nove.DeviceModel) -> tuple[int, ...]:
    """Physical qubits named by the device's connectivity or coordinates."""

    qubits: set[int] = set(device.qubit_coordinates)
    for a, b in device.connectivity:
        qubits.update((a, b))
    num = device.metadata.get("num_qubits") if isinstance(device.metadata, Mapping) else None
    if isinstance(num, int):
        qubits.update(range(num))
    return tuple(sorted(qubits))


def enumerate_placements(circuit: nove.Circuit, device: nove.DeviceModel) -> list[Placement]:
    """Every valid SWAP-free mapping, in lexicographic order (R-P2).

    Raises :class:`NoValidPlacementError` when there is none (R-P3).
    """

    n = circuit.num_qubits
    physical = device_qubits(device)
    adjacency: dict[int, set[int]] = {q: set() for q in physical}
    for a, b in device.connectivity:
        adjacency[a].add(b)
        adjacency[b].add(a)
    neighbours: list[list[int]] = [[] for _ in range(n)]
    for a, b in interaction_edges(circuit):
        neighbours[max(a, b)].append(min(a, b))

    results: list[Placement] = []
    current: list[int] = []
    used: set[int] = set()

    def extend(index: int) -> None:
        if index == n:
            results.append(tuple(current))
            return
        for q in physical:
            if q in used:
                continue
            if any(current[j] not in adjacency[q] for j in neighbours[index]):
                continue
            current.append(q)
            used.add(q)
            extend(index + 1)
            used.discard(q)
            current.pop()

    if n > len(physical):
        raise NoValidPlacementError(
            f"circuit needs {n} qubits but the device has {len(physical)}; no placement exists"
        )
    extend(0)
    if not results:
        raise NoValidPlacementError(
            "no SWAP-free placement exists: the circuit's interaction graph does not embed "
            f"in the connectivity of device {device.device_id!r}"
        )
    return results


def is_valid_placement(
    circuit: nove.Circuit, device: nove.DeviceModel, mapping: Sequence[int]
) -> bool:
    """R-P1: one-to-one, defined for every circuit qubit, gate pairs on connections."""

    if len(mapping) != circuit.num_qubits or len(set(mapping)) != len(mapping):
        return False
    known = set(device_qubits(device))
    if any(q not in known for q in mapping):
        return False
    return all(device.is_connected(mapping[a], mapping[b]) for a, b in interaction_edges(circuit))


def _map_target(target: stim.GateTarget, mapping: Sequence[int]) -> stim.GateTarget:
    if target.is_measurement_record_target or target.is_sweep_bit_target:
        return target
    if target.is_combiner:
        return target
    q = mapping[target.value]
    if target.is_x_target:
        return stim.target_x(q, invert=target.is_inverted_result_target)
    if target.is_y_target:
        return stim.target_y(q, invert=target.is_inverted_result_target)
    if target.is_z_target:
        return stim.target_z(q, invert=target.is_inverted_result_target)
    if target.is_qubit_target:
        return stim.target_inv(q) if target.is_inverted_result_target else stim.GateTarget(q)
    raise ValueError(f"unsupported target {target!r}")


def _relabel_stim(circuit: stim.Circuit, mapping: Sequence[int]) -> stim.Circuit:
    out = stim.Circuit()
    for item in circuit:
        if isinstance(item, stim.CircuitRepeatBlock):
            out.append(
                stim.CircuitRepeatBlock(item.repeat_count, _relabel_stim(item.body_copy(), mapping))
            )
            continue
        targets = [_map_target(t, mapping) for t in item.targets_copy()]
        out.append(stim.CircuitInstruction(item.name, targets, item.gate_args_copy(), tag=item.tag))
    return out


def relabel_circuit(circuit: nove.Circuit, mapping: Sequence[int]) -> nove.Circuit:
    """Return the circuit with circuit qubit ``c`` renamed to ``mapping[c]`` (R-D2).

    Instruction order, detectors, observables and measurement order are kept
    (R-P6). Coordinates attached to instructions are unchanged.
    """

    if len(mapping) != circuit.num_qubits or len(set(mapping)) != len(mapping):
        raise ValueError("mapping must be one-to-one and cover every circuit qubit")
    return nove.Circuit.from_stim(_relabel_stim(circuit.to_stim(), list(mapping)))


def unrelabel_circuit(
    mapped: nove.Circuit, mapping: Sequence[int], num_circuit_qubits: int
) -> nove.Circuit:
    """Inverse of :func:`relabel_circuit`."""

    inverse = [0] * (max(mapping) + 1)
    for c, q in enumerate(mapping):
        inverse[q] = c
    if len(mapping) != num_circuit_qubits:
        raise ValueError("mapping length differs from the circuit's qubit count")
    return nove.Circuit.from_stim(_relabel_stim(mapped.to_stim(), inverse))


def lowered_circuit(
    circuit: nove.Circuit,
    device: nove.DeviceModel,
    noise: nove.HardwareNoiseModel,
    mapping: Sequence[int],
) -> stim.Circuit:
    """Relabel and lower Nove calibrated noise into an ordinary Stim circuit."""

    experiment = nove.Experiment(
        relabel_circuit(circuit, mapping), noise=nove.NoiseModel(hardware=noise), device=device
    )
    return experiment.resolved_circuit().to_stim()


def approximate_dem(noisy: stim.Circuit, *, decompose: bool) -> stim.DetectorErrorModel:
    """Stim's approximate detector error model (exact one does not exist, K1)."""

    return noisy.detector_error_model(
        approximate_disjoint_errors=True,
        decompose_errors=decompose,
        ignore_decomposition_failures=decompose,
    )


def score_placement(
    circuit: nove.Circuit,
    device: nove.DeviceModel,
    noise: nove.HardwareNoiseModel,
    mapping: Sequence[int],
) -> float:
    """Selection score (lower is better); see the module docstring."""

    dem = approximate_dem(lowered_circuit(circuit, device, noise, mapping), decompose=False)
    total = 0.0
    for instruction in dem.flattened():
        if instruction.type == "error":
            total += float(instruction.args_copy()[0])
    return total


def _rounded(score: float) -> float:
    return float(f"{score:.{SCORE_SIGNIFICANT_DIGITS - 1}e}")


def place(
    circuit: nove.Circuit,
    device: nove.DeviceModel,
    noise: nove.HardwareNoiseModel,
) -> PlacementResult:
    """Choose a SWAP-free placement using only circuit, device and noise data (R-D1)."""

    mappings = enumerate_placements(circuit, device)
    candidates = tuple(
        Candidate(mapping=m, score=score_placement(circuit, device, noise, m)) for m in mappings
    )
    best = min(candidates, key=lambda c: (_rounded(c.score), c.mapping))
    return PlacementResult(mapping=best.mapping, score=best.score, candidates=candidates)


def fixed_placement(circuit: nove.Circuit, device: nove.DeviceModel) -> Placement:
    """R-B1: the first valid mapping in lexicographic order; uses no noise data."""

    return enumerate_placements(circuit, device)[0]


__all__ = [
    "SCORE_METHOD",
    "TIE_BREAK_RULE",
    "Candidate",
    "NoValidPlacementError",
    "Placement",
    "PlacementResult",
    "approximate_dem",
    "device_qubits",
    "enumerate_placements",
    "fixed_placement",
    "interaction_edges",
    "is_valid_placement",
    "lowered_circuit",
    "place",
    "relabel_circuit",
    "score_placement",
    "unrelabel_circuit",
]
