"""v2 Part B baselines that need no Qiskit: T2-B1 and R-B4/R-B6."""

from __future__ import annotations

import math

import pytest
from conftest import CIRCUITS, circuit_for

from noise_aware_compilation.baselines import lowest_summed_error, summed_error
from noise_aware_compilation.devices import StoredDevice
from noise_aware_compilation.placement import enumerate_placements, place


def _channel_total(device: StoredDevice, gate: str, qubits: tuple[int, ...]) -> float:
    params = next(
        p
        for key, p in device.snapshot.operation_parameters.items()
        if key.gate == gate and tuple(key.targets) == qubits
    )
    return math.fsum(v for k, v in params.items() if k.startswith("p_"))


def _hand_summed_error(device: StoredDevice, m: tuple[int, ...]) -> list[float]:
    """Circuit A (d=3, 3 rounds) written out by hand: data m0, m2, m4; ancillas m1, m3."""

    qp = device.snapshot.qubit_parameters
    reset = [qp[q]["reset_flip_probability"] for q in m]  # R on all five qubits
    reset += 2 * [qp[m[1]]["reset_flip_probability"], qp[m[3]]["reset_flip_probability"]]
    round_cx = [(m[0], m[1]), (m[2], m[3]), (m[2], m[1]), (m[4], m[3])]  # data -> ancilla
    cx = 3 * [_channel_total(device, "CX", pair) for pair in round_cx]
    readout = 3 * [qp[m[1]]["readout_flip_probability"], qp[m[3]]["readout_flip_probability"]]
    readout += [qp[q]["readout_flip_probability"] for q in (m[0], m[2], m[4])]
    return reset + cx + readout


def test_t2_b1_summed_error_matches_hand_calculation(devices: dict[str, StoredDevice]) -> None:
    jakarta = devices["jakarta"]
    circuit = circuit_for(3, "Z")
    fixed = enumerate_placements(circuit, jakarta.device)[0]
    chosen = place(circuit, jakarta.device, jakarta.noise).mapping
    assert fixed != chosen
    for mapping in (fixed, chosen):
        terms = _hand_summed_error(jakarta, mapping)
        assert len(terms) == 30
        assert summed_error(circuit, jakarta.noise, mapping) == math.fsum(terms)
        # and against the raw calibration: CX total = 1.25 e, readout as given, reset 0
        raw = 0.0
        for a, b in [(mapping[0], mapping[1]), (mapping[2], mapping[3])]:
            raw += 3 * 1.25 * _source(jakarta, a, b)
        for a, b in [(mapping[2], mapping[1]), (mapping[4], mapping[3])]:
            raw += 3 * 1.25 * _source(jakarta, a, b)
        qp = jakarta.snapshot.qubit_parameters
        raw += sum(3 * qp[mapping[i]]["source_readout_error"] for i in (1, 3))
        raw += sum(qp[mapping[i]]["source_readout_error"] for i in (0, 2, 4))
        assert summed_error(circuit, jakarta.noise, mapping) == pytest.approx(raw, rel=1e-12)


def _source(device: StoredDevice, a: int, b: int) -> float:
    return next(
        p["source_gate_error"]
        for key, p in device.snapshot.operation_parameters.items()
        if key.gate == "CX" and tuple(key.targets) == (a, b)
    )


@pytest.mark.parametrize(
    ("device", "label", "distance", "basis", "expected"),
    [c for c in CIRCUITS if c[1] != "C"],
    ids=[f"{d}-{c}" for d, c, *_ in CIRCUITS if c != "C"],
)
def test_r_b4_lowest_summed_error_draws_from_candidate_set(
    device: str,
    label: str,
    distance: int,
    basis: str,
    expected: int,
    devices: dict[str, StoredDevice],
) -> None:
    stored = devices[device]
    circuit = circuit_for(distance, basis)
    result = lowest_summed_error(circuit, stored.device, stored.noise)
    candidates = enumerate_placements(circuit, stored.device)
    assert [c.mapping for c in result.candidates] == candidates  # R-B6: same candidate set
    best = min(c.score for c in result.candidates)
    tied = sorted(c.mapping for c in result.candidates if c.score == best)
    assert result.mapping == tied[0] and result.score == best  # lexicographic tie-break
    assert len(result.candidates) == expected
