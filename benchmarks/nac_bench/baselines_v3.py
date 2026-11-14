"""Layout tools people actually use, run on the converted snapshot (roadmap Step 6b).

Two baselines join the v2 ones, both reading the same calibration as every
other method:

* **Qiskit's default transpile** at optimization levels 1, 2 and 3
  (``generate_preset_pass_manager`` with a fixed ``seed_transpiler``). The pass
  that produced the layout (``TrivialLayout``, ``VF2Layout``, ``SabreLayout`` or
  ``VF2PostLayout``) is recorded, so a reader can see when the default was
  noise-unaware. A layout that needed SWAPs is reported as not SWAP-free and
  has no counts; no substitute is used.
* **mapomatic 0.14.0**, used as its README does: transpile at level 3, deflate,
  ``matching_layouts``, ``evaluate_layouts`` with its ``default_cost``. It reads
  ``backend.properties()``, so :class:`SnapshotBackend` serves the converted
  snapshot's values through that interface.

Both run on :func:`native_target`, an IBM-shaped ``Target`` (``id``, ``rz``,
``sx``, ``x``, the two-qubit gate, ``measure``, ``reset``) whose errors are the
snapshot's *source* values, the numbers IBM published and the conversion read:
two-qubit and ``sx`` gate errors and readout errors as published, ``rz`` error
0, ``reset`` with no error (IBM publishes none). For couplers the conversion
kept, these equal the fake backend's own ``Target``; couplers it removed are
absent. This differs from v2's ``qiskit-vf2`` baseline, whose Target carries
the Nove channels' total error probabilities on an op-for-op circuit; that
baseline is kept as it was.

The selections need no simulation: each chosen placement is one of the
SWAP-free candidates v2 already measured, so its counts are looked up.
"""

from __future__ import annotations

import functools
import importlib.metadata
import itertools
import math
import time
import warnings
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import nove
from qiskit import QuantumCircuit, transpile
from qiskit.circuit import Parameter
from qiskit.circuit.library import CXGate, CZGate, IGate, Measure, Reset, RZGate, SXGate, XGate
from qiskit.providers.backend import QubitProperties
from qiskit.transpiler import CouplingMap, InstructionProperties, Target
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager

from nac_bench.qiskit_layout import qiskit_circuit
from noise_aware_compilation.placement import Placement, interaction_edges

NS = 1e-9
LEVELS = (1, 2, 3)
LAYOUT_PASSES = ("TrivialLayout", "VF2Layout", "SabreLayout", "DenseLayout", "SetLayout")
TWO_QUBIT_GATES = {"CX": ("cx", CXGate), "CZ": ("cz", CZGate)}
#: The package's tie rule (scores rounded to 12 significant digits), used to report
#: mapomatic's tied-best set; mapomatic's own pick is always its first sorted entry.
TIE_DIGITS = 12


def mapomatic_version() -> str:
    return importlib.metadata.version("mapomatic")


def _two_qubit_gate(snapshot: nove.CalibrationSnapshot) -> str:
    names = {key.gate for key in snapshot.operation_parameters if len(key.targets) == 2}
    if len(names) != 1 or next(iter(names)) not in TWO_QUBIT_GATES:
        raise ValueError(f"expected one two-qubit gate of {sorted(TWO_QUBIT_GATES)}, got {names}")
    return next(iter(names))


RESET_ERRORS = ("none", "readout")


def native_target(
    snapshot: nove.CalibrationSnapshot, device: nove.DeviceModel, reset_error: str = "none"
) -> Target:
    """An IBM-shaped Target carrying the snapshot's source (published) calibration values.

    ``reset_error="none"`` leaves ``reset`` without an error, as IBM publishes it;
    ``"readout"`` gives each qubit's reset its published readout error, the
    reset = readout setting of the v3 reset run.
    """

    if reset_error not in RESET_ERRORS:
        raise ValueError(f"reset_error must be one of {RESET_ERRORS}")

    num_qubits = len(snapshot.qubit_parameters)
    qubit = {int(q): p for q, p in snapshot.qubit_parameters.items()}
    if sorted(qubit) != list(range(num_qubits)):
        raise ValueError("snapshot qubits must be numbered 0..n-1")
    # P2-56: a qubit IBM publishes no T1 or T2 for has no coupler and gets neither
    properties = [
        QubitProperties(
            t1=float(qubit[q]["source_t1_ns"]) * NS if "source_t1_ns" in qubit[q] else None,
            t2=float(qubit[q]["source_t2_ns"]) * NS if "source_t2_ns" in qubit[q] else None,
        )
        for q in range(num_qubits)
    ]
    target = Target(num_qubits=num_qubits, qubit_properties=properties)
    gate = _two_qubit_gate(snapshot)
    _name, cls = TWO_QUBIT_GATES[gate]
    live = {(a, b) for a, b in device.connectivity} | {(b, a) for a, b in device.connectivity}
    two: dict[tuple[int, ...], InstructionProperties] = {}
    one: dict[tuple[int, ...], InstructionProperties] = {}
    for key, params in snapshot.operation_parameters.items():
        targets = tuple(int(t) for t in key.targets)
        if key.gate == gate:
            if targets not in live:
                raise ValueError(f"{gate}{targets} is not a live coupler of the device")
            two[targets] = InstructionProperties(
                error=float(params["source_gate_error"]),
                duration=float(params["source_gate_length_ns"]) * NS,
            )
        elif key.gate == "H":
            one[targets] = InstructionProperties(
                error=float(params["source_sx_gate_error"]),
                duration=float(params["source_sx_gate_length_ns"]) * NS,
            )
    if set(two) != live:
        raise ValueError("every live coupler needs a calibrated two-qubit gate in both directions")
    # P2-56: a removed qubit has no H channel; the Target gives it IBM's published sx error
    # (1.0) at the mean sx length, as IBM's own Target does, and it has no coupler
    removed = (device.metadata or {}).get("removed_qubits", ())
    if removed and one:
        mean_duration = sum(p.duration or 0.0 for p in one.values()) / len(one)
        for entry in removed:
            one.setdefault(
                (int(entry["qubit"]),),
                InstructionProperties(error=float(entry["sx_gate_error"]), duration=mean_duration),
            )
    if set(one) != {(q,) for q in range(num_qubits)}:
        raise ValueError("every qubit needs a calibrated single-qubit gate")
    target.add_instruction(cls(), two)
    for single in (IGate(), SXGate(), XGate()):
        target.add_instruction(
            single,
            {q: InstructionProperties(error=p.error, duration=p.duration) for q, p in one.items()},
        )
    target.add_instruction(
        RZGate(Parameter("theta")),
        {(q,): InstructionProperties(error=0.0, duration=0.0) for q in range(num_qubits)},
    )
    measure_ns = float(device.measurement_duration or 0.0)
    reset_ns = float(device.reset_duration or 0.0)
    target.add_instruction(
        Measure(),
        {
            (q,): InstructionProperties(
                error=float(qubit[q]["source_readout_error"]), duration=measure_ns * NS
            )
            for q in range(num_qubits)
        },
    )
    target.add_instruction(
        Reset(),
        {
            (q,): InstructionProperties(
                error=None if reset_error == "none" else float(qubit[q]["source_readout_error"]),
                duration=reset_ns * NS,
            )
            for q in range(num_qubits)
        },
    )
    return target


# --- what a transpiled circuit did -----------------------------------------------------------


@dataclass(frozen=True)
class Placed:
    """Where a transpiled circuit put each circuit qubit, and whether it needed SWAPs."""

    mapping: tuple[int, ...] | None
    swap_free: bool
    reason: str | None  # why it is not SWAP-free, or None


def placed(
    original: QuantumCircuit, out: QuantumCircuit, edges: Sequence[tuple[int, int]]
) -> Placed:
    """Read the placement from where each measurement landed, independent of layout bookkeeping.

    Every circuit qubit is measured. A clbit's first measurement in ``out`` is on the
    physical qubit holding the circuit qubit whose first measurement wrote that clbit in
    ``original``. The circuit is SWAP-free under that placement when it has the same
    number of two-qubit gates and each acts on the image of an interaction edge, and no
    circuit qubit's measurements moved.
    """

    def measured(qc: QuantumCircuit) -> dict[int, list[int]]:
        out_map: dict[int, list[int]] = {}
        for inst in qc.data:
            if inst.operation.name == "measure":
                c = qc.find_bit(inst.clbits[0]).index
                out_map.setdefault(c, []).append(qc.find_bit(inst.qubits[0]).index)
        return out_map

    before, after = measured(original), measured(out)
    mapping: dict[int, int] = {}
    moved = False
    for clbit, qubits in before.items():
        q = qubits[0]
        physical = after.get(clbit)
        if physical is None:
            return Placed(None, False, f"clbit {clbit} is never measured after transpiling")
        if len(set(physical)) != 1:
            moved = True
        if mapping.setdefault(q, physical[0]) != physical[0]:
            moved = True
    if sorted(mapping) != list(range(original.num_qubits)):
        return Placed(None, False, "some circuit qubits are never measured")
    result = tuple(mapping[q] for q in range(original.num_qubits))
    images = {frozenset((result[a], result[b])) for a, b in edges}
    gates = [
        frozenset(out.find_bit(q).index for q in inst.qubits)
        for inst in out.data
        if inst.operation.num_qubits == 2
    ]
    n_original = sum(1 for inst in original.data if inst.operation.num_qubits == 2)
    if moved:
        return Placed(result, False, "a circuit qubit's measurements moved (routing permuted it)")
    if len(gates) != n_original:
        return Placed(
            result, False, f"{len(gates)} two-qubit gates after transpiling, {n_original} before"
        )
    if any(g not in images for g in gates):
        return Placed(result, False, "a two-qubit gate acts outside the placed interaction edges")
    return Placed(result, True, None)


# --- Qiskit's default transpile ----------------------------------------------------------------


@dataclass(frozen=True)
class DefaultChoice:
    level: int
    seed_transpiler: int
    mapping: tuple[int, ...] | None
    swap_free: bool
    layout_pass: str  # the pass that produced the final layout
    passes_that_set_layout: tuple[str, ...]
    stop_reasons: dict[str, str]
    failure: str | None
    seconds: float


def _layout_value(layout: Any, qubits: Sequence[Any]) -> tuple[int, ...] | None:
    if layout is None:
        return None
    try:
        return tuple(int(layout[q]) for q in qubits)
    except KeyError:
        return None


def qiskit_default(
    qc: QuantumCircuit,
    target: Target,
    level: int,
    seed_transpiler: int,
    edges: Sequence[tuple[int, int]],
    candidates: set[Placement],
) -> DefaultChoice:
    """Run Qiskit's preset pass manager and record which pass produced the layout."""

    events: list[tuple[str, tuple[int, ...] | None]] = []
    stop: dict[str, str] = {}

    def callback(**kwargs: Any) -> None:
        ps = kwargs["property_set"]
        events.append((type(kwargs["pass_"]).__name__, _layout_value(ps["layout"], qc.qubits)))
        for key in ("VF2Layout_stop_reason", "VF2PostLayout_stop_reason"):
            if ps[key] is not None:
                stop[key] = str(getattr(ps[key], "value", ps[key]))

    manager = generate_preset_pass_manager(
        optimization_level=level, target=target, seed_transpiler=seed_transpiler
    )
    start = time.perf_counter()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        out = manager.run(qc, callback=callback)
    seconds = time.perf_counter() - start

    setters: list[str] = []
    layout: tuple[int, ...] | None = None
    for name, value in events:
        if name in LAYOUT_PASSES and value is not None and value != layout:
            setters.append(name)
            layout = value
    where = placed(qc, out, edges)
    if where.mapping is not None and where.mapping == layout and setters:
        chooser = setters[-1]
    elif where.mapping is not None and stop.get("VF2PostLayout_stop_reason") == "solution found":
        chooser = "VF2PostLayout"
    else:
        chooser = "unknown"
    failure = None
    if not where.swap_free:
        failure = f"not SWAP-free: {where.reason}"
    elif where.mapping not in candidates:
        failure = "outside candidate set"
    return DefaultChoice(
        level=level,
        seed_transpiler=seed_transpiler,
        mapping=where.mapping,
        swap_free=where.swap_free,
        layout_pass=chooser,
        passes_that_set_layout=tuple(setters),
        stop_reasons=stop,
        failure=failure,
        seconds=seconds,
    )


# --- mapomatic ---------------------------------------------------------------------------------


class _Properties:
    """The two ``BackendProperties`` methods mapomatic's ``default_cost`` calls."""

    def __init__(self, target: Target) -> None:
        self._target = target

    def gate_error(self, gate: str, qubits: int | Sequence[int]) -> float:
        qargs = (qubits,) if isinstance(qubits, int) else tuple(int(q) for q in qubits)
        props = self._target[gate][qargs]
        if props is None or props.error is None:
            raise KeyError(f"no {gate} error for {qargs}")
        return float(props.error)

    def readout_error(self, qubit: int) -> float:
        props = self._target["measure"][(int(qubit),)]
        if props is None or props.error is None:
            raise KeyError(f"no readout error for qubit {qubit}")
        return float(props.error)


@dataclass(frozen=True)
class _Configuration:
    backend_name: str
    num_qubits: int
    basis_gates: list[str]
    coupling_map: list[list[int]]
    simulator: bool = False


class SnapshotBackend:
    """Serves a :func:`native_target` through ``properties()`` and ``configuration()``.

    This is all mapomatic 0.14.0 reads from a backend; it is not a Qiskit backend.
    """

    def __init__(self, name: str, target: Target) -> None:
        self.target = target
        self._properties = _Properties(target)
        basis = sorted(n for n in target.operation_names if n not in ("measure", "reset", "delay"))
        edges = sorted([int(a), int(b)] for a, b in target.build_coupling_map().get_edges())
        self._configuration = _Configuration(name, target.num_qubits, basis, edges)

    def properties(self) -> _Properties:
        return self._properties

    def configuration(self) -> _Configuration:
        return self._configuration


@dataclass(frozen=True)
class MapomaticChoice:
    mapping: tuple[int, ...] | None
    cost: float | None
    num_layouts: int
    tied_best: tuple[tuple[int, ...], ...]  # every layout tied with the best (12 sig. digits)
    rank_of_pick_among_ties: int
    failure: str | None
    seconds: float
    costs: dict[tuple[int, ...], float] = field(repr=False, default_factory=dict)


def _rounded(x: float) -> float:
    return float(f"{x:.{TIE_DIGITS}g}")


ChunkMap = Callable[[QuantumCircuit, list[list[list[int]]]], Iterable[list[tuple[Any, float]]]]


def evaluate_layouts_in_chunks(
    small: QuantumCircuit,
    layouts: list[list[int]],
    backend: SnapshotBackend,
    map_chunks: ChunkMap,
    chunk_size: int,
) -> list[tuple[Any, float]]:
    """``mapomatic.evaluate_layouts`` with its default cost computed chunk by chunk.

    The default cost scores each layout on its own, in order, so concatenating the chunks
    and applying mapomatic's own stable sort gives the same list as one call.
    """

    if not any(layouts):
        return []
    gates = set(small.count_ops()).difference({"barrier", "reset", "measure", "delay"})
    if not gates.issubset(backend.configuration().basis_gates):
        return []
    if not isinstance(layouts[0], list):
        layouts = [layouts]
    chunks = [layouts[i : i + chunk_size] for i in range(0, len(layouts), chunk_size)]
    out = [pair for part in map_chunks(small, chunks) for pair in part]
    out.sort(key=lambda x: x[1])
    return out


@functools.cache
def _stored_backend(device_name: str, reset_error: str) -> SnapshotBackend:
    """The Target of a stored device; ``"directional"`` is the revision 6 setting (its
    readout and reset errors at each qubit's P(1|0), :mod:`nac_bench.directional`)."""

    from noise_aware_compilation.devices import load_device

    stored = load_device(device_name)
    snapshot = stored.snapshot
    if reset_error == "directional":
        from nac_bench.directional import directional_snapshot

        snapshot, reset_error = directional_snapshot(stored, device_name), "readout"
    target = native_target(snapshot, stored.device, reset_error)
    return SnapshotBackend(str(snapshot.device_id), target)


def mapomatic_cost_chunk(
    args: tuple[str, str, QuantumCircuit, list[list[int]]],
) -> list[tuple[Any, float]]:
    """mapomatic's default cost of one chunk of layouts, on a stored device (a pool task)."""

    from mapomatic.layouts import default_cost

    device_name, reset_error, small, layouts = args
    return list(default_cost(small, layouts, _stored_backend(device_name, reset_error)))


def mapomatic_choice(
    qc: QuantumCircuit,
    backend: SnapshotBackend,
    seed_transpiler: int,
    edges: Sequence[tuple[int, int]],
    candidates: set[Placement],
    map_chunks: ChunkMap | None = None,
    chunk_size: int = 20_000,
) -> MapomaticChoice:
    """mapomatic's README workflow on ``backend``; the pick is its lowest-cost layout.

    With ``map_chunks`` the layouts' costs are computed in chunks (for a process pool);
    the result is the same.
    """

    import mapomatic as mm

    start = time.perf_counter()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        trans = transpile(
            qc, target=backend.target, optimization_level=3, seed_transpiler=seed_transpiler
        )
        where = placed(qc, trans, edges)
        if not where.swap_free or where.mapping is None:
            return MapomaticChoice(
                None,
                None,
                0,
                (),
                -1,
                f"transpiled circuit is not SWAP-free: {where.reason}",
                time.perf_counter() - start,
            )
        small = mm.deflate_circuit(trans)
        active = sorted(where.mapping)
        if small.num_qubits != len(active):
            return MapomaticChoice(
                None,
                None,
                0,
                (),
                -1,
                "deflated circuit has extra active qubits",
                time.perf_counter() - start,
            )
        deflated_index = [active.index(p) for p in where.mapping]
        layouts = mm.matching_layouts(small, CouplingMap(backend.configuration().coupling_map))
        if map_chunks is None:
            scored = mm.evaluate_layouts(small, layouts, backend)
        else:
            scored = evaluate_layouts_in_chunks(small, layouts, backend, map_chunks, chunk_size)
    seconds = time.perf_counter() - start
    if not scored:
        return MapomaticChoice(None, None, len(layouts), (), -1, "no layout scored", seconds)

    def ours(layout: Sequence[int]) -> tuple[int, ...]:
        return tuple(int(layout[d]) for d in deflated_index)

    costs = {ours(layout): float(cost) for layout, cost in scored}
    best_layout, best_cost = scored[0]
    pick = ours(best_layout)
    tied = tuple(ours(lay) for lay, c in scored if _rounded(float(c)) == _rounded(float(best_cost)))
    failure = None if pick in candidates else "outside candidate set"
    return MapomaticChoice(
        mapping=pick,
        cost=float(best_cost),
        num_layouts=len(layouts),
        tied_best=tied,
        rank_of_pick_among_ties=tied.index(pick),
        failure=failure,
        seconds=seconds,
        costs=costs,
    )


# --- one case ----------------------------------------------------------------------------------


def run_baselines(
    circuit: nove.Circuit,
    snapshot: nove.CalibrationSnapshot,
    device: nove.DeviceModel,
    candidates: Sequence[Placement],
    seed_transpiler: int,
    seed_sweep: Sequence[int] = (),
    reset_error: str = "none",
    map_chunks: ChunkMap | None = None,
    top: int = 0,
    sweep_levels: Sequence[int] = LEVELS,
) -> dict[str, Any]:
    """Every Step 6b selection for one case, as a JSON-ready record.

    ``map_chunks`` computes mapomatic's layout costs in chunks (same result); its tasks
    rebuild the backend from the stored device, so it needs a stored device. ``top`` keeps
    mapomatic's first ``top`` layouts in its own order (lowest cost first, its stable sort).
    ``seed_sweep`` re-runs the levels in ``sweep_levels`` with each of its transpiler seeds
    and records every mapping and failure (revision 6, R3-C14).
    """

    qc = qiskit_circuit(circuit)
    edges = sorted(interaction_edges(circuit))
    allowed = set(candidates)
    target = native_target(snapshot, device, reset_error)
    record: dict[str, Any] = {}
    for level in LEVELS:
        c = qiskit_default(qc, target, level, seed_transpiler, edges, allowed)
        runs = (
            [qiskit_default(qc, target, level, s, edges, allowed) for s in seed_sweep]
            if level in sweep_levels
            else []
        )
        sweep = [r.mapping for r in runs]
        record[f"qiskit-default-l{level}"] = {
            "mapping": None if c.mapping is None else list(c.mapping),
            "swap_free": c.swap_free,
            "layout_pass": c.layout_pass,
            "passes_that_set_layout": list(c.passes_that_set_layout),
            "stop_reasons": c.stop_reasons,
            "failure": c.failure,
            "seed_transpiler": seed_transpiler,
            "seed_sweep": list(seed_sweep) if runs else [],
            "seed_sweep_distinct_mappings": len({m for m in sweep if m is not None}),
            "seed_sweep_matches_main": sum(1 for m in sweep if m == c.mapping),
            "seed_sweep_runs": [
                {
                    "seed_transpiler": r.seed_transpiler,
                    "mapping": None if r.mapping is None else list(r.mapping),
                    "layout_pass": r.layout_pass,
                    "failure": r.failure,
                }
                for r in runs
            ],
            "selection_seconds": c.seconds,
        }
    backend = SnapshotBackend(str(snapshot.device_id), target)
    m = mapomatic_choice(qc, backend, seed_transpiler, edges, allowed, map_chunks)
    record["mapomatic"] = {
        "mapping": None if m.mapping is None else list(m.mapping),
        "cost": m.cost,
        "num_layouts": m.num_layouts,
        "tied_best": [list(t) for t in m.tied_best],
        "rank_of_pick_among_ties": m.rank_of_pick_among_ties,
        "failure": m.failure,
        "seed_transpiler": seed_transpiler,
        "selection_seconds": m.seconds,
        "candidate_costs": None
        if not m.costs
        else [m.costs.get(tuple(p), math.nan) for p in candidates],
    }
    if top:
        record["mapomatic"]["top_layouts"] = [
            list(layout) for layout in itertools.islice(m.costs, top)
        ]
    return record


__all__ = [
    "LEVELS",
    "RESET_ERRORS",
    "DefaultChoice",
    "MapomaticChoice",
    "Placed",
    "SnapshotBackend",
    "evaluate_layouts_in_chunks",
    "mapomatic_choice",
    "mapomatic_cost_chunk",
    "mapomatic_version",
    "native_target",
    "placed",
    "qiskit_default",
    "run_baselines",
]
