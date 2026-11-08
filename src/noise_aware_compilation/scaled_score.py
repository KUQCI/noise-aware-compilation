"""Scaled decoder-graph score (v3 Part C, SPEC_V3.md R3-S1).

The v2 decoder-graph score (:mod:`noise_aware_compilation.decoder_graph`) has two
parts that grow exponentially: it enumerates every minimum-length logical cycle, and
it sums over every subset of each cycle's edges. Surface codes need neither to be
exhaustive, so the scaled score replaces both:

1. **Matching graph.** The same graph as the v2 score: graphlike pieces of the placed
   circuit's decomposed approximate detector error model, merged as independent
   events, with undetectable logical errors added directly.
2. **Chains.** A logical error the decoder cannot see is a closed path through the
   boundary that flips a logical observable. In the *doubled graph*, whose nodes are
   (detector, observables flipped so far), such a path is a path from (boundary, none)
   to (boundary, some). With edge weights ``log((1 - p) / p)``, Dijkstra finds the
   lightest (most likely) one, and Yen's k-shortest-paths search the next ones in
   order of weight. A path that visits a detector twice (in two layers) is not a
   simple cycle and is skipped; each cycle is found once in each direction and
   counted once. The ``k`` lightest cycles are kept.
3. **Failure probability of a chain.** As in v2, the chain contributes
   P(w(S) > w(C) / 2) + P(w(S) = w(C) / 2) / 2 for the flipped edge set S. Here the
   distribution of w(S) is a dynamic program over the edge weights rounded to a grid
   of ``step``, linear in the chain length times its weight in grid units.
4. **Score** = undetectable logical probability + the sum over the ``k`` chains (a
   union bound, as in v2). It uses no sampling.

The score is a ranking signal for comparing placements of one circuit on one device,
not an estimate of the logical error rate (R3-S7).
"""

from __future__ import annotations

import heapq
import itertools
import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import nove
import numpy as np

from noise_aware_compilation.decoder_graph import (
    BOUNDARY,
    EdgeKey,
    MatchingGraph,
    matching_graph,
)
from noise_aware_compilation.placement import Scorer, approximate_dem, lowered_circuit

SCALED_METHOD = (
    "Scaled decoder-graph score: undetectable logical errors plus, for the k lightest closed "
    "paths through the boundary that flip a logical observable (Yen's k-shortest paths on the "
    "doubled matching graph), the probability that the errors on each outweigh half of it, from "
    "a dynamic program over grid-rounded edge weights. A ranking signal, not an error rate."
)

Node = tuple[int, int]  # (detector or BOUNDARY, observable mask)


def edge_weight(probability: float) -> float:
    """``log((1 - p) / p)``, floored at 0 so that every path search stays valid."""

    if probability >= 0.5:
        return 0.0
    return math.log((1.0 - probability) / probability)


@dataclass(frozen=True)
class DoubledGraph:
    """The doubled matching graph: node ids, adjacency and the original edge of each edge."""

    nodes: tuple[Node, ...]
    adjacency: tuple[tuple[tuple[int, float], ...], ...]  # node id -> (neighbour id, weight)
    edge_keys: dict[tuple[int, int], EdgeKey]  # (u, v), u < v -> original edge
    source: int
    sink: int  # a virtual node joined at weight 0 to every (boundary, mask != 0)


def doubled_graph(graph: MatchingGraph) -> DoubledGraph:
    """Build the doubled graph over every (detector, mask) state reachable from the boundary."""

    flips = {key[2] for key in graph.edges}
    masks = {0}
    while True:
        grown = masks | {m ^ f for m in masks for f in flips}
        if grown == masks:
            break
        masks = grown
    detectors = sorted({n for key in graph.edges for n in key[:2] if n != BOUNDARY})
    nodes: list[Node] = [(BOUNDARY, m) for m in sorted(masks)]
    nodes += [(d, m) for d in detectors for m in sorted(masks)]
    index = {node: i for i, node in enumerate(nodes)}
    sink = len(nodes)
    neighbours: list[dict[int, float]] = [{} for _ in range(sink + 1)]
    edge_keys: dict[tuple[int, int], EdgeKey] = {}
    for key in sorted(graph.edges):
        a, b, flip = key
        w = edge_weight(graph.edges[key])
        for m in sorted(masks):
            u, v = index[(a, m)], index[(b, m ^ flip)]
            neighbours[u][v] = w
            neighbours[v][u] = w
            edge_keys[(min(u, v), max(u, v))] = key
    for m in sorted(masks):
        if m:
            u = index[(BOUNDARY, m)]
            neighbours[u][sink] = 0.0
            neighbours[sink][u] = 0.0
    adjacency = tuple(tuple(sorted(n.items())) for n in neighbours)
    return DoubledGraph(
        nodes=tuple(nodes),
        adjacency=adjacency,
        edge_keys=edge_keys,
        source=index[(BOUNDARY, 0)],
        sink=sink,
    )


def _dijkstra(
    graph: DoubledGraph,
    start: int,
    blocked_nodes: set[int],
    blocked_edges: set[tuple[int, int]],
) -> tuple[float, list[int]] | None:
    distance = {start: 0.0}
    previous: dict[int, int] = {}
    heap = [(0.0, start)]
    done: set[int] = set()
    while heap:
        d, u = heapq.heappop(heap)
        if u in done:
            continue
        if u == graph.sink:
            path = [u]
            while path[-1] != start:
                path.append(previous[path[-1]])
            return d, path[::-1]
        done.add(u)
        for v, w in graph.adjacency[u]:
            if v in blocked_nodes or v in done or (u, v) in blocked_edges:
                continue
            nd = d + w
            if nd < distance.get(v, math.inf):
                distance[v] = nd
                previous[v] = u
                heapq.heappush(heap, (nd, v))
    return None


def _path_weight(graph: DoubledGraph, path: Sequence[int]) -> float:
    total = 0.0
    for u, v in itertools.pairwise(path):
        total += dict(graph.adjacency[u])[v]
    return total


def shortest_simple_paths(graph: DoubledGraph) -> Iterator[tuple[float, tuple[int, ...]]]:
    """Yen's algorithm: simple source-to-sink paths in order of weight (ties by node ids)."""

    first = _dijkstra(graph, graph.source, set(), set())
    if first is None:
        return
    found: list[tuple[int, ...]] = [tuple(first[1])]
    yield first[0], found[0]
    candidates: list[tuple[float, tuple[int, ...]]] = []
    seen = {found[0]}
    while True:
        last = found[-1]
        for i in range(len(last) - 1):
            spur, root = last[i], last[: i + 1]
            blocked_edges: set[tuple[int, int]] = set()
            for path in found:
                if path[: i + 1] == root:
                    blocked_edges.add((path[i], path[i + 1]))
                    blocked_edges.add((path[i + 1], path[i]))
            spur_path = _dijkstra(graph, spur, set(root[:-1]), blocked_edges)
            if spur_path is None:
                continue
            total = root[:-1] + tuple(spur_path[1])
            if total not in seen:
                seen.add(total)
                heapq.heappush(candidates, (_path_weight(graph, root) + spur_path[0], total))
        if not candidates:
            return
        weight, path = heapq.heappop(candidates)
        found.append(path)
        yield weight, path


def lightest_logical_chains(graph: MatchingGraph, k: int) -> list[tuple[EdgeKey, ...]]:
    """The ``k`` lightest simple logical cycles through the boundary, lightest first."""

    if k < 1 or not graph.edges:
        return []
    doubled = doubled_graph(graph)
    chains: list[tuple[EdgeKey, ...]] = []
    counted: set[frozenset[EdgeKey]] = set()
    for _weight, path in shortest_simple_paths(doubled):
        inner = path[1:-2]  # drop (boundary, 0), (boundary, m) and the sink
        detectors = [doubled.nodes[u][0] for u in inner]
        if BOUNDARY in detectors or len(set(detectors)) != len(detectors):
            continue  # visits a detector (or the boundary) twice: not a simple cycle
        edges = [
            doubled.edge_keys[(min(u, v), max(u, v))] for u, v in itertools.pairwise(path[:-1])
        ]
        key = frozenset(edges)
        if key in counted:
            continue  # the same cycle in the other direction
        counted.add(key)
        chains.append(tuple(sorted(edges)))
        if len(chains) == k:
            break
    return chains


def chain_failure_probability(probabilities: Sequence[float], step: float) -> float:
    """P(flipped weight > half the chain) + P(= half) / 2, by a DP over grid-rounded weights."""

    if step <= 0:
        raise ValueError("step must be positive")
    units = [round(edge_weight(p) / step) for p in probabilities]
    total = sum(units)
    distribution = np.zeros(total + 1)
    distribution[0] = 1.0
    for p, n in zip(probabilities, units, strict=True):
        if n == 0:
            continue
        shifted = np.zeros_like(distribution)
        shifted[n:] = distribution[: total + 1 - n]
        distribution = (1.0 - p) * distribution + p * shifted
    sums = 2 * np.arange(total + 1)
    above = math.fsum(distribution[sums > total])
    ties = math.fsum(distribution[sums == total])
    return above + 0.5 * ties


@dataclass(frozen=True)
class ScaledParameters:
    k: int
    step: float


def scaled_score_from_graph(graph: MatchingGraph, parameters: ScaledParameters) -> float:
    chains = lightest_logical_chains(graph, parameters.k)
    terms = [
        chain_failure_probability([graph.edges[e] for e in chain], parameters.step)
        for chain in chains
    ]
    return math.fsum([graph.undetectable_logical, *terms])


def scaled_score(
    circuit: nove.Circuit,
    device: nove.DeviceModel,
    noise: nove.HardwareNoiseModel,
    mapping: Sequence[int],
    parameters: ScaledParameters,
) -> float:
    """Scaled score of one placement; see the module docstring. Lower is better."""

    noisy = lowered_circuit(circuit, device, noise, mapping)
    graph = matching_graph(approximate_dem(noisy, decompose=True))
    return scaled_score_from_graph(graph, parameters)


def scaled_scorer(parameters: ScaledParameters) -> Scorer:
    def function(
        circuit: nove.Circuit,
        device: nove.DeviceModel,
        noise: nove.HardwareNoiseModel,
        mapping: Sequence[int],
    ) -> float:
        return scaled_score(circuit, device, noise, mapping, parameters)

    return Scorer(
        name=f"scaled-k{parameters.k}-step{parameters.step:g}",
        method=f"{SCALED_METHOD} k = {parameters.k}, grid step {parameters.step:g}.",
        function=function,
    )


__all__ = [
    "SCALED_METHOD",
    "DoubledGraph",
    "ScaledParameters",
    "chain_failure_probability",
    "doubled_graph",
    "edge_weight",
    "lightest_logical_chains",
    "scaled_score",
    "scaled_score_from_graph",
    "scaled_scorer",
    "shortest_simple_paths",
]
