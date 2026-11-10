"""SPEC_V3.md revision 5, B3: the idle-aware summed error on development devices.

Its value is summed error plus the total probability of every idle channel, where an idle
channel is a single-qubit Pauli channel on a qubit that no gate acts on in that layer.
"""

from __future__ import annotations

import math

import pytest
import stim

from nac_bench.run_reset import noise_for
from noise_aware_compilation.baselines import (
    idle_aware_summed_error,
    noise_location_terms,
    summed_error,
)
from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.devices import load_device
from noise_aware_compilation.fast_lowering import Lowering
from noise_aware_compilation.placement import enumerate_placements


def _idle_total(noisy: stim.Circuit) -> float:
    total: list[float] = []
    busy: set[int] = set()
    for instruction in noisy.flattened():
        name = instruction.name
        if name == "TICK":
            busy = set()
            continue
        targets = [t.value for t in instruction.targets_copy() if t.is_qubit_target]
        data = stim.gate_data(name)
        if data.is_unitary:
            busy.update(targets)
        elif name == "PAULI_CHANNEL_1":
            p = math.fsum(instruction.gate_args_copy())
            total += [p for q in targets if q not in busy]
    return math.fsum(total)


@pytest.mark.parametrize(
    ("device", "distance", "setting"), [("fez", 3, "readout"), ("miami", 5, "zero")]
)
def test_idle_aware_is_summed_error_plus_idles(device: str, distance: int, setting: str) -> None:
    stored = load_device(device)
    noise = noise_for(setting, stored)
    circuit = repetition_code(distance, 3, "Z", "CZ")
    lowering = Lowering(circuit, stored.device, noise)
    for mapping in enumerate_placements(circuit, stored.device)[:25]:
        noisy = lowering.stim_circuit(mapping)
        value = idle_aware_summed_error(noisy)
        idle = _idle_total(noisy)
        assert idle > 0
        assert value == pytest.approx(summed_error(circuit, noise, mapping) + idle, rel=1e-12)


def test_unknown_noise_is_refused() -> None:
    for text in ("HERALDED_ERASE(0.1) 0", "E(0.1) X0", "MPP(0.1) Z0*Z1"):
        with pytest.raises(ValueError, match="no idle-aware"):
            noise_location_terms(stim.Circuit(text))


def test_measurement_and_channels_count_once_per_target() -> None:
    noisy = stim.Circuit(
        "R 0 1\nX_ERROR(0.01) 0 1\nPAULI_CHANNEL_2(" + ",".join(["0.001"] * 15) + ") 0 1\n"
        "PAULI_CHANNEL_1(0.1, 0.1, 0.2) 0\nM(0.05) 0 1"
    )
    assert noise_location_terms(noisy) == pytest.approx([0.01, 0.01, 0.015, 0.4, 0.05, 0.05])
