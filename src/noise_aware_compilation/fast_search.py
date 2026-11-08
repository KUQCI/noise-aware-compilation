"""Fast search for a repetition-code placement (SPEC_V3.md R3-C8, roadmap Step 2.3).

Exhaustive selection scores every placement. This search scores few, and is reported
beside the exhaustive choice as exploratory (and is R3-C4's fallback for a case whose
exhaustive selection would take too long):

1. **Pre-filter.** Every placement gets its summed error (``baselines.summed_error``'s
   terms, added per qubit and per coupler with NumPy, so its last bits can differ from
   ``math.fsum``'s; it only orders the search). The ``prefilter`` lowest are scored.
   Summed error can point at the wrong part of the chip (on FakeFez the decoder-graph
   choice is in the worst third by summed error), so ``spread`` more are scored: walking
   down the summed-error order, each placement that shares no qubit with an earlier one
   picked this way, until ``spread`` are picked or none is left.
2. **Local search.** From each of the ``starts`` best placements scored so far, steepest descent:
   score every neighbour, move to the best if it beats the current placement, stop when
   none does. A repetition code's interaction graph is a path, so a placement is a simple
   path of 2d - 1 device qubits, and its neighbours are the paths that

   * run the other way (the mirror placement);
   * slide by 1 to ``max_shift`` qubits in either direction (drop that many qubits at
     one end, add as many new ones at the other, every way the device allows);
   * swap an end qubit for another neighbour of the next one;
   * swap an inner qubit for another common neighbour of its two path neighbours (a
     corner flip, possible on the square lattice, not on heavy-hex).

3. The choice is the best placement scored, by the same rule as exhaustive selection:
   lowest score rounded to 12 significant digits, ties to the lexicographically smallest
   mapping. At most ``max_evaluations`` placements are scored.

Every neighbour is a valid placement (one to one, every interaction pair on a coupler),
so the choice is always one of the exhaustive candidates.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

import nove
import numpy as np
import stim

from noise_aware_compilation.placement import Placement, _rounded, interaction_edges


@dataclass(frozen=True)
class SearchParameters:
    prefilter: int
    starts: int
    spread: int = 0
    max_shift: int = 2
    max_evaluations: int = 20_000


@dataclass(frozen=True)
class SearchResult:
    mapping: Placement
    score: float
    evaluations: int
    starts: tuple[Placement, ...]
    descents: tuple[int, ...]  # moves taken from each start
    scored: dict[Placement, float] = field(repr=False, default_factory=dict)


class NotAPathError(ValueError):
    """The circuit's interaction graph is not a simple path."""


def path_order(circuit: nove.Circuit) -> tuple[int, ...]:
    """The circuit's qubits along its interaction path, from the end with the smaller index."""

    adjacency: dict[int, list[int]] = {q: [] for q in range(circuit.num_qubits)}
    for a, b in interaction_edges(circuit):
        adjacency[a].append(b)
        adjacency[b].append(a)
    ends = sorted(q for q, v in adjacency.items() if len(v) == 1)
    if len(ends) != 2 or any(len(v) > 2 for v in adjacency.values()):
        raise NotAPathError("the interaction graph is not a path")
    order = [ends[0]]
    previous = -1
    while len(order) < circuit.num_qubits:
        here = order[-1]
        nxt = next(q for q in adjacency[here] if q != previous)
        previous = here
        order.append(nxt)
    return tuple(order)


def _iter_flat(circuit: stim.Circuit) -> Iterable[stim.CircuitInstruction]:
    for item in circuit:
        if isinstance(item, stim.CircuitRepeatBlock):
            body = item.body_copy()
            for _ in range(item.repeat_count):
                yield from _iter_flat(body)
        else:
            yield item


class SummedErrorTables:
    """``baselines.summed_error`` as per-qubit and per-coupler tables, for many placements."""

    def __init__(
        self, circuit: nove.Circuit, device: nove.DeviceModel, noise: nove.HardwareNoiseModel
    ) -> None:
        self.n = circuit.num_qubits
        size = 1 + max(max(p) for p in device.connectivity)
        size = max(size, int(device.metadata.get("num_qubits", 0) or 0))
        single: dict[tuple[int, str, str], int] = {}  # (circuit qubit, kind, gate) -> count
        double: dict[tuple[int, int, str], int] = {}  # (circuit qubit a, b, gate) -> count
        for instruction in _iter_flat(circuit.to_stim()):
            name = instruction.name
            try:
                data = stim.gate_data(name)
            except IndexError:
                continue
            qubits = [t.value for t in instruction.targets_copy() if t.is_qubit_target]
            if data.is_reset and not data.produces_measurements:
                kind = "reset"
            elif data.produces_measurements:
                kind = "readout"
            elif data.is_unitary:
                kind = "gate"
            else:
                continue
            if kind == "gate" and data.is_two_qubit_gate:
                for i in range(0, len(qubits), 2):
                    key = (qubits[i], qubits[i + 1], name)
                    double[key] = double.get(key, 0) + 1
            else:
                for q in qubits:
                    one = (q, kind, name)
                    single[one] = single.get(one, 0) + 1
        self.per_qubit = np.zeros((self.n, size))
        for (c, kind, name), count in sorted(single.items()):
            for q in range(size):
                if kind == "reset":
                    term = noise.reset_probability_for(q) or 0.0
                elif kind == "readout":
                    term = noise.readout_probability_for(q) or 0.0
                else:
                    channel = noise.gate_channel_for(nove.OperationKey(name, (q,)))
                    term = 0.0 if channel is None else float(sum(channel.probabilities))
                self.per_qubit[c, q] += count * term
        self.pairs: list[tuple[int, int, np.ndarray]] = []
        for a, b, name in sorted({(a, b, name) for a, b, name in double}):
            table = np.zeros((size, size))
            for x, y in device.connectivity:
                for u, v in ((x, y), (y, x)):
                    channel = noise.gate_channel_for(nove.OperationKey(name, (u, v)))
                    if channel is not None:
                        table[u, v] = double[(a, b, name)] * float(sum(channel.probabilities))
            self.pairs.append((a, b, table))

    def values(self, placements: np.ndarray) -> np.ndarray:
        """Summed error of each row of ``placements`` (rows are mappings)."""

        total = np.zeros(len(placements))
        for c in range(self.n):
            total += self.per_qubit[c, placements[:, c]]
        for a, b, table in self.pairs:
            total += table[placements[:, a], placements[:, b]]
        return total


class FastSearch:
    """R3-C8's search for one circuit, device and noise model."""

    def __init__(
        self,
        circuit: nove.Circuit,
        device: nove.DeviceModel,
        noise: nove.HardwareNoiseModel,
        placements: Sequence[Placement],
    ) -> None:
        self.order = path_order(circuit)
        self.n = circuit.num_qubits
        self.placements = placements
        self.tables = SummedErrorTables(circuit, device, noise)
        self.adjacency: dict[int, frozenset[int]] = {}
        neighbours: dict[int, set[int]] = {}
        for a, b in device.connectivity:
            neighbours.setdefault(a, set()).add(b)
            neighbours.setdefault(b, set()).add(a)
        self.adjacency = {q: frozenset(v) for q, v in neighbours.items()}

    def prefiltered(self, count: int) -> list[Placement]:
        """The ``count`` placements of lowest summed error, ties in enumeration order."""

        array = np.asarray(self.placements, dtype=np.int64)
        values = self.tables.values(array)
        top = np.argsort(values, kind="stable")[:count]
        return [self.placements[int(i)] for i in top]

    def spread_out(self, count: int) -> list[Placement]:
        """Up to ``count`` placements on disjoint qubits, each the lowest in summed error
        among those sharing no qubit with the ones before it."""

        array = np.asarray(self.placements, dtype=np.int64)
        order = np.argsort(self.tables.values(array), kind="stable")
        taken: set[int] = set()
        picked: list[Placement] = []
        for i in order:
            if len(picked) >= count:
                break
            m = self.placements[int(i)]
            if taken.isdisjoint(m):
                picked.append(m)
                taken.update(m)
        return picked

    def _mapping(self, path: Sequence[int]) -> Placement:
        mapping = [0] * self.n
        for position, c in enumerate(self.order):
            mapping[c] = path[position]
        return tuple(mapping)

    def _path(self, mapping: Placement) -> list[int]:
        return [mapping[c] for c in self.order]

    def neighbours(self, mapping: Placement, max_shift: int = 2) -> list[Placement]:
        """Every placement one move away, in a fixed order, without duplicates."""

        p = self._path(mapping)
        adj = self.adjacency
        out: list[list[int]] = [p[::-1]]

        def grow(path: list[int], taken: set[int], k: int) -> Iterable[list[int]]:
            if k == 0:
                yield path
                return
            for v in sorted(adj.get(path[-1], frozenset()) - taken):
                taken.add(v)
                yield from grow([*path, v], taken, k - 1)
                taken.discard(v)

        for s in range(1, min(max_shift, len(p) - 1) + 1):
            for forward in (True, False):
                kept = p[s:] if forward else p[:-s][::-1]
                for grown in grow(kept, set(kept), s):
                    out.append(grown if forward else grown[::-1])
        used = set(p)
        for v in sorted(adj.get(p[1], frozenset()) - used):
            out.append([v, *p[1:]])
        for v in sorted(adj.get(p[-2], frozenset()) - used):
            out.append([*p[:-1], v])
        for i in range(1, len(p) - 1):
            for v in sorted(
                (adj.get(p[i - 1], frozenset()) & adj.get(p[i + 1], frozenset())) - used
            ):
                out.append([*p[:i], v, *p[i + 1 :]])
        seen: set[Placement] = {mapping}
        result: list[Placement] = []
        for path in out:
            m = self._mapping(path)
            if m not in seen:
                seen.add(m)
                result.append(m)
        return result

    def run(
        self, score: Callable[[Placement], float], parameters: SearchParameters
    ) -> SearchResult:
        scored: dict[Placement, float] = {}

        def key(m: Placement) -> tuple[float, Placement]:
            return (_rounded(scored[m]), m)

        def evaluate(ms: Iterable[Placement]) -> list[Placement]:
            done = []
            for m in ms:
                if m not in scored:
                    if len(scored) >= parameters.max_evaluations:
                        break
                    scored[m] = score(m)
                done.append(m)
            return done

        pool = evaluate(self.prefiltered(parameters.prefilter))
        pool += evaluate(self.spread_out(parameters.spread))
        starts = sorted(set(pool), key=key)[: parameters.starts]
        descents = []
        for start in starts:
            current, moves = start, 0
            while len(scored) < parameters.max_evaluations:
                options = evaluate(self.neighbours(current, parameters.max_shift))
                if not options:
                    break
                best = min(options, key=key)
                if key(best) >= key(current):
                    break
                current, moves = best, moves + 1
            descents.append(moves)
        choice = min(scored, key=key)
        return SearchResult(
            mapping=choice,
            score=scored[choice],
            evaluations=len(scored),
            starts=tuple(starts),
            descents=tuple(descents),
            scored=scored,
        )


__all__ = [
    "FastSearch",
    "NotAPathError",
    "SearchParameters",
    "SearchResult",
    "SummedErrorTables",
    "path_order",
]
