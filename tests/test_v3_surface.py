"""v3 rotated surface code (SPEC_V3.md R3-S4): layout, schedules, CZ form and embeddings."""

from __future__ import annotations

import nove
import pytest
import stim

from noise_aware_compilation.devices import load_device
from noise_aware_compilation.placement import (
    NoValidPlacementError,
    enumerate_placements,
    interaction_edges,
)
from noise_aware_compilation.surface_code import (
    ALL_SCHEDULES,
    DEFAULT_SCHEDULE,
    circuit_distance,
    hook_safe_schedules,
    surface_code,
    surface_code_stim,
    surface_layout,
)

Pair = tuple[tuple[int, int], tuple[int, int]]


def _cx_layers(circuit: stim.Circuit, coords: dict[int, tuple[int, int]]) -> list[list[Pair]]:
    layers = []
    for inst in circuit.flattened():
        if inst.name == "CX":
            t = [x.value for x in inst.targets_copy()]
            layers.append(sorted((coords[t[i]], coords[t[i + 1]]) for i in range(0, len(t), 2)))
    return layers


@pytest.mark.parametrize("distance", [3, 5])
def test_default_schedule_is_stims_rotated_memory_z(distance: int) -> None:
    layout = surface_layout(distance)
    assert len(layout.coords) == 2 * distance**2 - 1
    assert len(layout.data) == distance**2
    assert len(layout.x_ancillas) == len(layout.z_ancillas) == (distance**2 - 1) // 2
    ours = surface_code_stim(distance, 3, DEFAULT_SCHEDULE, "CX")
    ref = stim.Circuit.generated("surface_code:rotated_memory_z", distance=distance, rounds=3)
    ref_coords = {q: (int(c[0]), int(c[1])) for q, c in ref.get_final_qubit_coordinates().items()}
    assert _cx_layers(ours, dict(enumerate(layout.coords))) == _cx_layers(ref, ref_coords)
    assert ours.num_detectors == ref.num_detectors
    assert ours.num_observables == ref.num_observables == 1


@pytest.mark.parametrize("schedule", ALL_SCHEDULES)
@pytest.mark.parametrize("gate", ["CX", "CZ"])
def test_every_schedule_is_deterministic(schedule: tuple[str, str], gate: str) -> None:
    circuit = surface_code_stim(3, 3, schedule, gate)  # type: ignore[arg-type]
    circuit.detector_error_model()
    dets, obs = circuit.compile_detector_sampler(seed=1).sample(64, separate_observables=True)
    assert not dets.any() and not obs.any()


def test_hook_safe_schedules_keep_the_full_distance() -> None:
    assert hook_safe_schedules(5, 5, "CZ") == (("Z", "N"), ("Z", "Z"))
    assert hook_safe_schedules(3, 3, "CZ") == (("Z", "N"), ("Z", "Z"))
    # an N-shaped X-stabilizer order lets a hook error halve the distance
    assert circuit_distance(surface_code_stim(5, 5, ("N", "Z"), "CZ")) == 3


def test_cz_form_keeps_the_cx_two_qubit_layers() -> None:
    cx = surface_code_stim(5, 2, DEFAULT_SCHEDULE, "CX")
    cz = surface_code_stim(5, 2, DEFAULT_SCHEDULE, "CZ")
    assert {i.name for i in cz.flattened()} <= {
        "R", "H", "CZ", "M", "TICK", "DETECTOR", "OBSERVABLE_INCLUDE"
    }  # fmt: skip

    def pairs(c: stim.Circuit, name: str) -> list[set[frozenset[int]]]:
        out = []
        for inst in c.flattened():
            if inst.name == name:
                t = [x.value for x in inst.targets_copy()]
                out.append({frozenset(t[i : i + 2]) for i in range(0, len(t), 2)})
        return out

    assert pairs(cz, "CZ") == pairs(cx, "CX")
    assert circuit_distance(cz) == 5


def test_surface_code_embeddings_on_miami() -> None:
    stored = load_device("miami")
    d3 = surface_code(3, 3)
    placements = enumerate_placements(d3, stored.device)
    assert len(placements) == 176
    # every d=5 embedding of FakeMiami's 10 x 12 lattice crosses one of its 7 removed couplers
    d5 = surface_code(5, 5)
    with pytest.raises(NoValidPlacementError):
        enumerate_placements(d5, stored.device)
    removed = [tuple(e["pair"]) for e in stored.device.metadata["removed_couplers"]]
    full = nove.DeviceModel(
        connectivity=[*stored.device.connectivity, *removed],
        metadata={"num_qubits": 120},
    )
    every = enumerate_placements(d5, full)
    assert len(every) == 64  # 8 shifts x 8 rotations and reflections
    edges = interaction_edges(d5)
    dead = {frozenset(p) for p in removed}
    assert all(any(frozenset((m[a], m[b])) in dead for a, b in edges) for m in every)
