"""SPEC_V3.md revision 5, B3, and revision 6: the idle-aware summed error on development devices.

Its value is summed error plus the total probability of every idle channel, where an idle
channel is a single-qubit Pauli channel, in a layer where no gate acts on its qubit, on a
qubit the placed circuit uses. Revision 6 (P2-41) leaves out the channels the lowering puts
on every other device qubit up to the highest index, which tracked that index.
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


def _used(noisy: stim.Circuit) -> set[int]:
    used: set[int] = set()
    for instruction in noisy.flattened():
        data = stim.gate_data(instruction.name) if instruction.name != "TICK" else None
        if data is not None and (data.is_unitary or data.is_reset or data.produces_measurements):
            used.update(t.value for t in instruction.targets_copy() if t.is_qubit_target)
    return used


def _idle_total(noisy: stim.Circuit) -> float:
    total: list[float] = []
    busy: set[int] = set()
    used = _used(noisy)
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
            total += [p for q in targets if q not in busy and q in used]
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


def test_unused_qubits_do_not_count() -> None:
    """Revision 6: the value does not track the placement's highest qubit index."""

    stored = load_device("fez")
    noise = noise_for("readout", stored)
    circuit = repetition_code(3, 3, "Z", "CZ")
    lowering = Lowering(circuit, stored.device, noise)
    placements = enumerate_placements(circuit, stored.device)
    low, high = placements[0], max(placements, key=max)
    for mapping in (low, high):
        noisy = lowering.stim_circuit(mapping)
        idle_channels = sum(
            len(i.targets_copy()) for i in noisy.flattened() if i.name == "PAULI_CHANNEL_1"
        )
        assert idle_channels > 0
        assert _used(noisy) == set(mapping)
    assert lowering.stim_circuit(high).num_qubits > 10 * len(high)
    values = [idle_aware_summed_error(lowering.stim_circuit(m)) for m in (low, high)]
    # before revision 6 the high placement scored about 40 times the low one
    assert max(values) < 3 * min(values)


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
