"""Decoder-graph selection score (v2 Part C, R-G1, R-G2).

The score estimates a placement's logical failure probability under a
minimum-weight matching decoder, from the placed circuit's detector error
model alone. No sampling, simulation or decoder is used (R-G2, R-I6).

1. **Matching graph.** The placed circuit is lowered with the Nove noise model
   and Stim's approximate detector error model is decomposed into graphlike
   pieces (as the evaluation decoder's model is built, v1 decoder rule). Each
   piece with one or two detectors is an edge (one detector: an edge to the
   boundary) carrying a probability and the set of logical observables it
   flips. Pieces landing on the same detectors with the same observables are
   merged as independent events, p = p1 + p2 - 2 p1 p2. A piece that flips an
   observable but no detector is an undetectable logical error and is added to
   the score directly. Pieces with more than two detectors (failed
   decompositions) are not graphlike and are left out.
2. **Logical cycles.** A logical error the decoder cannot see is a set of
   edges forming a closed path through the boundary whose observable flips
   add up to a non-zero set. The score uses the cycles with the fewest edges
   (the leading order, the code distance in edges).
3. **Failure probability of a cycle.** With weights w = log((1 - p) / p), a
   minimum-weight decoder that sees the syndrome of an error set S on a cycle C
   corrects with C \\ S when that is lighter than S, which flips the logical.
   So the cycle contributes P(w(S) > w(C) / 2), plus half of P(w(S) = w(C) / 2)
   for ties, computed exactly over all subsets S of its edges with independent
   edge flips.
4. **Score** = undetectable logical probability + the sum over minimum-length
   logical cycles of their failure probabilities (a union bound). It accounts
   for which mechanisms flip an observable (R-G1). Lower is better.
"""

from __future__ import annotations

import itertools
import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

import nove
import stim

from noise_aware_compilation.placement import Scorer, approximate_dem, lowered_circuit

BOUNDARY = -1
TIE_TOLERANCE = 1e-12

DECODER_GRAPH_METHOD = (
    "Leading-order logical failure probability of a minimum-weight matching decoder, from the "
    "decomposed approximate detector error model of the placed circuit: undetectable logical "
    "errors plus, for every minimum-length closed path through the boundary that flips a "
    "logical observable, the probability that the errors on it outweigh half the path."
)

#: (detector, detector or BOUNDARY, observable mask)
EdgeKey = tuple[int, int, int]


@dataclass(frozen=True)
class MatchingGraph:
    """Graphlike edges of a detector error model and its undetectable logical probability."""

    edges: dict[EdgeKey, float]
    undetectable_logical: float
    non_graphlike: int


def _xor_probability(first: float, second: float) -> float:
    return first + second - 2.0 * first * second


def matching_graph(dem: stim.DetectorErrorModel) -> MatchingGraph:
    """Build the matching graph of a decomposed detector error model."""

    edges: dict[EdgeKey, float] = {}
    undetectable = 0.0
    non_graphlike = 0
    for instruction in dem.flattened():
        if instruction.type != "error":
            continue
        probability = float(instruction.args_copy()[0])
        if probability <= 0.0:
            continue
        pieces: list[list[stim.DemTarget]] = [[]]
        for target in instruction.targets_copy():
            if target.is_separator():
                pieces.append([])
            else:
                pieces[-1].append(target)
        for piece in pieces:
            detectors = sorted(t.val for t in piece if t.is_relative_detector_id())
            mask = 0
            for t in piece:
                if t.is_logical_observable_id():
                    mask ^= 1 << t.val
            if not detectors:
                if mask:
                    undetectable = _xor_probability(undetectable, probability)
                continue
            if len(detectors) > 2:
                non_graphlike += 1
                continue
            key = (detectors[0], detectors[1] if len(detectors) == 2 else BOUNDARY, mask)
            edges[key] = _xor_probability(edges.get(key, 0.0), probability)
    return MatchingGraph(
        edges=edges, undetectable_logical=undetectable, non_graphlike=non_graphlike
    )


def _other(key: EdgeKey, node: int) -> int:
    return key[1] if key[0] == node else key[0]


def logical_cycles(graph: MatchingGraph) -> list[tuple[EdgeKey, ...]]:
    """Every minimum-length simple closed path through the boundary that flips an observable.

    Each cycle is returned once, as its edge keys in sorted order; the list is sorted.
    """

    adjacency: dict[int, list[EdgeKey]] = defaultdict(list)
    for key in sorted(graph.edges):
        adjacency[key[0]].append(key)
        adjacency[key[1]].append(key)

    # Lower bound on the length: BFS over (node, observable mask) from the boundary. The
    # walk it finds may revisit a detector, so lengths are then tried upwards from it.
    distance = {(BOUNDARY, 0): 0}
    frontier = [(BOUNDARY, 0)]
    bound: int | None = None
    while frontier and bound is None:
        following = []
        for node, mask in frontier:
            for key in adjacency[node]:
                nxt, nxt_mask = _other(key, node), mask ^ key[2]
                if nxt == BOUNDARY:
                    if nxt_mask and bound is None:
                        bound = distance[(node, mask)] + 1
                elif (nxt, nxt_mask) not in distance:
                    distance[(nxt, nxt_mask)] = distance[(node, mask)] + 1
                    following.append((nxt, nxt_mask))
        frontier = following
    if bound is None:
        return []

    found: set[tuple[EdgeKey, ...]] = set()

    def extend(node: int, visited: set[int], path: list[EdgeKey], mask: int, length: int) -> None:
        for key in adjacency[node]:
            nxt = _other(key, node)
            if nxt == BOUNDARY:
                if len(path) + 1 == length and mask ^ key[2]:
                    found.add(tuple(sorted([*path, key])))
                continue
            if nxt in visited or len(path) + 1 >= length:
                continue
            visited.add(nxt)
            path.append(key)
            extend(nxt, visited, path, mask ^ key[2], length)
            path.pop()
            visited.discard(nxt)

    detectors = {n for key in graph.edges for n in key[:2] if n != BOUNDARY}
    for length in range(bound, len(detectors) + 2):
        for key in adjacency[BOUNDARY]:
            first = _other(key, BOUNDARY)
            extend(first, {first}, [key], key[2], length)
        if found:
            break
    return sorted(found)


def cycle_failure_probability(probabilities: Sequence[float]) -> float:
    """P(the flipped edges outweigh half the cycle) for independent edge flips.

    Exact sum over all subsets; ties (flipped weight exactly half) count half.
    """

    weights = [math.log((1.0 - p) / p) for p in probabilities]
    half = math.fsum(weights) / 2.0
    tolerance = TIE_TOLERANCE * max(1.0, abs(half))
    terms: list[float] = []
    for flips in itertools.product((False, True), repeat=len(probabilities)):
        flipped = math.fsum(w for w, f in zip(weights, flips, strict=True) if f)
        if flipped < half - tolerance:
            continue
        probability = math.prod(
            p if f else 1.0 - p for p, f in zip(probabilities, flips, strict=True)
        )
        terms.append(probability if flipped > half + tolerance else 0.5 * probability)
    return math.fsum(terms)


def decoder_graph_score_from_dem(dem: stim.DetectorErrorModel) -> float:
    """Decoder-graph score of a decomposed detector error model (lower is better)."""

    graph = matching_graph(dem)
    terms = [
        cycle_failure_probability([graph.edges[key] for key in cycle])
        for cycle in logical_cycles(graph)
    ]
    return math.fsum([graph.undetectable_logical, *terms])


def decoder_graph_score(
    circuit: nove.Circuit,
    device: nove.DeviceModel,
    noise: nove.HardwareNoiseModel,
    mapping: Sequence[int],
) -> float:
    """Decoder-graph score of one placement; see the module docstring."""

    noisy = lowered_circuit(circuit, device, noise, mapping)
    return decoder_graph_score_from_dem(approximate_dem(noisy, decompose=True))


DECODER_GRAPH_SCORER = Scorer(
    name="decoder-graph", method=DECODER_GRAPH_METHOD, function=decoder_graph_score
)


__all__ = [
    "BOUNDARY",
    "DECODER_GRAPH_METHOD",
    "DECODER_GRAPH_SCORER",
    "MatchingGraph",
    "cycle_failure_probability",
    "decoder_graph_score",
    "decoder_graph_score_from_dem",
    "logical_cycles",
    "matching_graph",
]
