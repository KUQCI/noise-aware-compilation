"""The prepared lowering (fast_lowering.py) gives Nove's circuit exactly (SPEC_V3.md R3-C4)."""

from __future__ import annotations

import random

import nove
import pytest
import stim

from nac_bench.run_reset import noise_for
from nac_bench.surface_check import restored
from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.decoder_graph import (
    decoder_graph_score,
    decoder_graph_score_from_dem,
)
from noise_aware_compilation.devices import available_devices, load_device
from noise_aware_compilation.fast_lowering import (
    Lowering,
    UnsupportedLoweringError,
    v1_score_from_dem,
)
from noise_aware_compilation.placement import (
    approximate_dem,
    enumerate_placements,
    lowered_circuit,
    score_placement,
)
from noise_aware_compilation.surface_code import ALL_SCHEDULES, surface_code

SAMPLE = 12
CIRCUITS = [(3, "Z", 3), (3, "X", 3), (5, "Z", 1), (5, "Z", 3), (7, "Z", 3)]


def _gate(stored: object) -> str:
    return "CZ" if stored.device.duration_for("CZ") is not None else "CX"  # type: ignore[attr-defined]


def _sample(placements: list[tuple[int, ...]], seed: int) -> list[tuple[int, ...]]:
    rng = random.Random(seed)
    return rng.sample(placements, min(SAMPLE, len(placements)))


def _assert_same(
    circuit: nove.Circuit,
    device: nove.DeviceModel,
    noise: nove.HardwareNoiseModel,
    mappings: list[tuple[int, ...]],
) -> None:
    lowering = Lowering(circuit, device, noise)
    for m in mappings:
        assert lowering.stim_circuit(m) == lowered_circuit(circuit, device, noise, m), m


@pytest.mark.parametrize("setting", ["zero", "readout"])
@pytest.mark.parametrize("name", available_devices())
def test_repetition_codes_match_nove_on_every_device(name: str, setting: str) -> None:
    stored = load_device(name)
    noise = noise_for(setting, stored)
    gate = _gate(stored)
    for distance, basis, rounds in CIRCUITS:
        if distance == 7 and name == "miami":
            continue  # 8.5 million placements to enumerate; FakeFez covers d=7 on CZ
        circuit = repetition_code(distance, rounds, basis, two_qubit_gate=gate)  # type: ignore[arg-type]
        if 2 * distance - 1 > stored.device.metadata["num_qubits"]:
            continue  # FakeJakarta has 7 qubits
        placements = enumerate_placements(circuit, stored.device)
        _assert_same(circuit, stored.device, noise, _sample(placements, distance + rounds))


@pytest.mark.parametrize("schedule", ALL_SCHEDULES)
def test_surface_codes_match_nove(schedule: tuple[str, str]) -> None:
    stored = load_device("miami")
    noise = noise_for("readout", stored)
    d3 = surface_code(3, 3, schedule)  # type: ignore[arg-type]
    _assert_same(d3, stored.device, noise, _sample(enumerate_placements(d3, stored.device), 1))
    device, repaired = restored("miami")
    d5 = surface_code(5, 5, schedule)  # type: ignore[arg-type]
    _assert_same(d5, device, repaired, _sample(enumerate_placements(d5, device), 2))


def test_scores_from_the_fast_circuit_equal_the_frozen_scores() -> None:
    stored = load_device("hanoi")
    noise = stored.noise
    circuit = repetition_code(5, 3, "Z")
    lowering = Lowering(circuit, stored.device, noise)
    for m in _sample(enumerate_placements(circuit, stored.device), 3):
        fast = lowering.stim_circuit(m)
        assert score_placement(circuit, stored.device, noise, m) == v1_score_from_dem(
            approximate_dem(fast, decompose=False)
        )
        assert decoder_graph_score(circuit, stored.device, noise, m) == (
            decoder_graph_score_from_dem(approximate_dem(fast, decompose=True))
        )
        lowering.check(m)


def test_refuses_what_it_does_not_reproduce() -> None:
    stored = load_device("hanoi")
    pauli = nove.Circuit.from_stim(stim.Circuit("R 0 1\nTICK\nCORRELATED_ERROR(0.1) X0 Z1\nM 0 1"))
    with pytest.raises(UnsupportedLoweringError):
        Lowering(pauli, stored.device, stored.noise)
    with pytest.raises(ValueError, match="one-to-one"):
        Lowering(repetition_code(3, 1), stored.device, stored.noise).text((0, 1, 1, 2, 3))
