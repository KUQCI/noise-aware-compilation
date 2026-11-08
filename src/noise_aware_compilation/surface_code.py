"""Rotated surface-code memory circuits for square-lattice devices (SPEC_V3.md R3-S4).

Layout, in Stim's coordinates for ``surface_code:rotated_memory_z``: data qubits at
(2i + 1, 2j + 1) for 0 <= i, j < d, ancillas at even (x, y) in [0, 2d]. An ancilla
is X-type when (x + y) / 2 is odd. Every interior ancilla is used; on the left and
right edges only Z-type ancillas, on the top and bottom edges only X-type ones, and
no corner. Each ancilla touches the data qubits at (x +- 1, y +- 1), which a
square-lattice device places on its four grid neighbours.

A round resets the ancillas (the first round resets everything), applies H to the
X-type ancillas, four CX layers (X-type: ancilla -> data, Z-type: data -> ancilla),
H again and measures the ancillas. Detectors compare each ancilla with its previous
round (Z-type ancillas also with their deterministic first round); the data are
measured at the end, each Z stabilizer's data parity is compared with its last
round, and the logical observable is the top data row (y = 1), as in Stim's
generated circuit.

**Gate orders.** Each stabilizer type visits its data qubits in one of two orders:
``"Z"``, row by row, (+1, +1), (-1, +1), (+1, -1), (-1, -1) in (dx, dy), and ``"N"``,
column by column, (+1, +1), (+1, -1), (-1, +1), (-1, -1). A schedule names the
order of the X-type and the Z-type stabilizers; ``("Z", "N")`` is Stim's and the
default. :func:`hook_safe_schedules` keeps the schedules that need no fifth layer,
measure every stabilizer correctly and keep the full circuit-level distance under
uniform noise, the hook-safe ones.

``two_qubit_gate="CZ"`` builds the circuit natively for CZ devices with
:func:`noise_aware_compilation.circuits.cz_native`.
"""

from __future__ import annotations

import functools
from collections import deque
from dataclasses import dataclass
from typing import Literal

import nove
import stim

from noise_aware_compilation.circuits import TwoQubitGate, cz_native

Shape = Literal["Z", "N"]
SHAPES: dict[str, tuple[tuple[int, int], ...]] = {
    "Z": ((1, 1), (-1, 1), (1, -1), (-1, -1)),
    "N": ((1, 1), (1, -1), (-1, 1), (-1, -1)),
}
Schedule = tuple[Shape, Shape]  # (X-type order, Z-type order)
ALL_SCHEDULES: tuple[Schedule, ...] = (("Z", "N"), ("N", "Z"), ("Z", "Z"), ("N", "N"))
DEFAULT_SCHEDULE: Schedule = ("Z", "N")


class InvalidScheduleError(ValueError):
    """The schedule puts one data qubit in two CX gates of one layer."""


@dataclass(frozen=True)
class SurfaceLayout:
    distance: int
    coords: tuple[tuple[int, int], ...]  # circuit qubit -> (x, y)
    data: tuple[int, ...]
    x_ancillas: tuple[int, ...]
    z_ancillas: tuple[int, ...]

    @property
    def ancillas(self) -> tuple[int, ...]:
        return tuple(sorted(self.x_ancillas + self.z_ancillas))


def _is_x_type(x: int, y: int) -> bool:
    return ((x + y) // 2) % 2 == 1


@functools.cache
def surface_layout(distance: int) -> SurfaceLayout:
    """Qubits of the distance-``d`` rotated patch, numbered in breadth-first order."""

    if distance < 2:
        raise ValueError("distance must be at least 2")
    top = 2 * distance
    data = {(2 * i + 1, 2 * j + 1) for i in range(distance) for j in range(distance)}
    ancillas: set[tuple[int, int]] = set()
    for x in range(0, top + 1, 2):
        for y in range(0, top + 1, 2):
            interior = 0 < x < top and 0 < y < top
            side = x in (0, top) and 0 < y < top
            end = y in (0, top) and 0 < x < top
            if interior or (side and not _is_x_type(x, y)) or (end and _is_x_type(x, y)):
                ancillas.add((x, y))
    sites = data | ancillas

    def neighbours(site: tuple[int, int]) -> list[tuple[int, int]]:
        x, y = site
        near = [(x + dx, y + dy) for dx in (-1, 1) for dy in (-1, 1)]
        return sorted((s for s in near if s in sites), key=lambda s: (s[1], s[0]))

    start = min(data, key=lambda s: (s[1], s[0]))
    order: list[tuple[int, int]] = []
    seen = {start}
    queue = deque([start])
    while queue:
        site = queue.popleft()
        order.append(site)
        for nxt in neighbours(site):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    if len(order) != len(sites):
        raise RuntimeError("the patch's interaction graph is not connected")
    index = {s: i for i, s in enumerate(order)}
    return SurfaceLayout(
        distance=distance,
        coords=tuple(order),
        data=tuple(sorted(index[s] for s in data)),
        x_ancillas=tuple(sorted(index[s] for s in ancillas if _is_x_type(*s))),
        z_ancillas=tuple(sorted(index[s] for s in ancillas if not _is_x_type(*s))),
    )


def _cx_layers(layout: SurfaceLayout, schedule: Schedule) -> list[list[tuple[int, int]]]:
    where = {c: q for q, c in enumerate(layout.coords)}
    data = set(layout.data)
    layers = []
    for step in range(4):
        pairs: list[tuple[int, int]] = []
        used: set[int] = set()
        for shape, ancillas, ancilla_controls in (
            (schedule[0], layout.x_ancillas, True),
            (schedule[1], layout.z_ancillas, False),
        ):
            dx, dy = SHAPES[shape][step]
            for a in ancillas:
                x, y = layout.coords[a]
                d = where.get((x + dx, y + dy))
                if d is None or d not in data:
                    continue
                if d in used:
                    raise InvalidScheduleError(
                        f"schedule {schedule} needs data {d} twice in a layer"
                    )
                used.add(d)
                pairs.append((a, d) if ancilla_controls else (d, a))
        layers.append(pairs)
    return layers


def surface_code_stim(
    distance: int,
    rounds: int,
    schedule: Schedule = DEFAULT_SCHEDULE,
    two_qubit_gate: TwoQubitGate = "CX",
) -> stim.Circuit:
    """The Z-memory experiment as a Stim circuit (R, H, CX or CZ, M only)."""

    if rounds < 1:
        raise ValueError("rounds must be at least 1")
    if two_qubit_gate not in ("CX", "CZ"):
        raise ValueError("two_qubit_gate must be 'CX' or 'CZ'")
    layout = surface_layout(distance)
    layers = _cx_layers(layout, schedule)
    ancillas = list(layout.ancillas)
    position = {a: i for i, a in enumerate(ancillas)}
    n_anc = len(ancillas)
    c = stim.Circuit()
    c.append("R", list(range(len(layout.coords))))
    c.append("TICK")
    for r in range(rounds):
        if r > 0:
            c.append("R", ancillas)
            c.append("TICK")
        c.append("H", list(layout.x_ancillas))
        c.append("TICK")
        for pairs in layers:
            c.append("CX", [q for pair in pairs for q in pair])
            c.append("TICK")
        c.append("H", list(layout.x_ancillas))
        c.append("TICK")
        c.append("M", ancillas)
        c.append("TICK")
        for a in ancillas:
            if r == 0 and a in layout.x_ancillas:
                continue
            x, y = layout.coords[a]
            targets = [stim.target_rec(-n_anc + position[a])]
            if r > 0:
                targets.append(stim.target_rec(-2 * n_anc + position[a]))
            c.append("DETECTOR", targets, [x, y, r])
    data = list(layout.data)
    c.append("M", data)
    n_data = len(data)
    data_position = {q: i for i, q in enumerate(data)}
    where = {coord: q for q, coord in enumerate(layout.coords)}
    for a in layout.z_ancillas:
        x, y = layout.coords[a]
        support = [where[(x + dx, y + dy)] for dx, dy in SHAPES["Z"] if (x + dx, y + dy) in where]
        targets = [stim.target_rec(-n_data + data_position[q]) for q in sorted(support)]
        targets.append(stim.target_rec(-n_data - n_anc + position[a]))
        c.append("DETECTOR", targets, [x, y, rounds])
    logical = [q for q in data if layout.coords[q][1] == 1]
    c.append(
        "OBSERVABLE_INCLUDE", [stim.target_rec(-n_data + data_position[q]) for q in logical], 0
    )
    return cz_native(c) if two_qubit_gate == "CZ" else c


def surface_code(
    distance: int,
    rounds: int,
    schedule: Schedule = DEFAULT_SCHEDULE,
    two_qubit_gate: TwoQubitGate = "CZ",
) -> nove.Circuit:
    """The rotated surface-code Z-memory experiment as a Nove circuit."""

    return nove.Circuit.from_stim(surface_code_stim(distance, rounds, schedule, two_qubit_gate))


def _with_uniform_noise(circuit: stim.Circuit, p: float) -> stim.Circuit:
    out = stim.Circuit()
    for inst in circuit.flattened():
        targets = inst.targets_copy()
        if inst.name == "M":
            out.append("X_ERROR", targets, p)
        out.append(inst)
        if inst.name == "R":
            out.append("X_ERROR", targets, p)
        elif inst.name == "H":
            out.append("DEPOLARIZE1", targets, p)
        elif inst.name in ("CX", "CZ"):
            out.append("DEPOLARIZE2", targets, p)
    return out


def circuit_distance(circuit: stim.Circuit) -> int:
    """Fewest faults of a logical error no detector sees, under uniform circuit noise."""

    errors = _with_uniform_noise(circuit, 1e-3).search_for_undetectable_logical_errors(
        dont_explore_detection_event_sets_with_size_above=4,
        dont_explore_edges_with_degree_above=4,
        dont_explore_edges_increasing_symptom_degree=False,
    )
    return len(errors)


@functools.cache
def hook_safe_schedules(
    distance: int, rounds: int, two_qubit_gate: TwoQubitGate = "CZ"
) -> tuple[Schedule, ...]:
    """The schedules with four layers, deterministic detectors and circuit distance ``d``."""

    safe = []
    for schedule in ALL_SCHEDULES:
        try:
            circuit = surface_code_stim(distance, rounds, schedule, two_qubit_gate)
        except InvalidScheduleError:
            continue
        try:
            circuit.detector_error_model()
        except ValueError:
            continue  # a stabilizer is measured wrongly: non-deterministic detectors
        if circuit_distance(circuit) == distance:
            safe.append(schedule)
    return tuple(safe)


__all__ = [
    "ALL_SCHEDULES",
    "DEFAULT_SCHEDULE",
    "SHAPES",
    "InvalidScheduleError",
    "Schedule",
    "SurfaceLayout",
    "circuit_distance",
    "hook_safe_schedules",
    "surface_code",
    "surface_code_stim",
    "surface_layout",
]
