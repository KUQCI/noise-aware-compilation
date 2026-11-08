"""v3 scaled score (SPEC_V3.md R3-S1): the dynamic program and the chain search."""

from __future__ import annotations

import itertools
import math
import random
from collections import defaultdict

import pytest

from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.decoder_graph import (
    BOUNDARY,
    EdgeKey,
    MatchingGraph,
    cycle_failure_probability,
    logical_cycles,
    matching_graph,
)
from noise_aware_compilation.devices import load_device
from noise_aware_compilation.placement import (
    approximate_dem,
    enumerate_placements,
    lowered_circuit,
)
from noise_aware_compilation.scaled_score import (
    ScaledParameters,
    chain_failure_probability,
    doubled_graph,
    edge_weight,
    lightest_logical_chains,
    scaled_score,
    scaled_score_from_graph,
    shortest_simple_paths,
)


def _graph(distance: int, rounds: int, device: str = "hanoi") -> MatchingGraph:
    stored = load_device(device)
    circuit = repetition_code(distance, rounds, "Z")
    mapping = enumerate_placements(circuit, stored.device)[3]
    noisy = lowered_circuit(circuit, stored.device, stored.noise, mapping)
    return matching_graph(approximate_dem(noisy, decompose=True))


def _all_logical_cycles(graph: MatchingGraph) -> list[tuple[EdgeKey, ...]]:
    """Brute force: every simple closed path through the boundary that flips an observable."""

    adjacency: dict[int, list[EdgeKey]] = defaultdict(list)
    for key in graph.edges:
        adjacency[key[0]].append(key)
        adjacency[key[1]].append(key)
    found: set[tuple[EdgeKey, ...]] = set()

    def walk(node: int, visited: set[int], path: list[EdgeKey], mask: int) -> None:
        for key in adjacency[node]:
            nxt = key[1] if key[0] == node else key[0]
            if nxt == BOUNDARY:
                if mask ^ key[2] and len(path) >= 1:
                    found.add(tuple(sorted([*path, key])))
                continue
            if nxt in visited:
                continue
            visited.add(nxt)
            path.append(key)
            walk(nxt, visited, path, mask ^ key[2])
            path.pop()
            visited.discard(nxt)

    for key in adjacency[BOUNDARY]:
        first = key[1] if key[0] == BOUNDARY else key[0]
        walk(first, {first}, [key], key[2])
    return sorted(found)


def _weight(graph: MatchingGraph, chain: tuple[EdgeKey, ...]) -> float:
    return math.fsum(edge_weight(graph.edges[e]) for e in chain)


def test_dp_matches_the_exact_subset_sum_on_a_fine_grid() -> None:
    rng = random.Random(7)
    for length in (1, 2, 3, 5, 7, 9):
        p = [rng.uniform(1e-4, 0.05) for _ in range(length)]
        exact = cycle_failure_probability(p)
        assert chain_failure_probability(p, 1e-4) == pytest.approx(exact, rel=1e-3)


def test_dp_counts_exact_ties_half() -> None:
    # two equal edges: flipping one is a tie (half), both outweigh half the chain
    p = 0.01
    assert chain_failure_probability([p, p], 0.1) == pytest.approx(p * p + 0.5 * 2 * p * (1 - p))
    assert chain_failure_probability([p, p], 0.1) == pytest.approx(
        cycle_failure_probability([p, p])
    )


def test_dp_rejects_a_non_positive_step() -> None:
    with pytest.raises(ValueError):
        chain_failure_probability([0.01], 0.0)


def test_yen_yields_paths_in_order_of_weight() -> None:
    doubled = doubled_graph(_graph(3, 2))
    first = list(itertools.islice(shortest_simple_paths(doubled), 300))
    weights = [w for w, _ in first]
    paths = [p for _, p in first]
    assert len(first) > 100  # 126 simple paths exist in this small graph
    assert all(a <= b + 1e-12 for a, b in itertools.pairwise(weights))
    assert len(set(paths)) == len(paths)
    assert all(p[0] == doubled.source and p[-1] == doubled.sink for p in paths)
    assert all(len(set(p)) == len(p) for p in paths)


@pytest.mark.parametrize(("distance", "rounds"), [(3, 1), (3, 2)])
def test_chain_search_finds_the_lightest_simple_logical_cycles(distance: int, rounds: int) -> None:
    graph = _graph(distance, rounds)
    brute = sorted(_all_logical_cycles(graph), key=lambda c: (_weight(graph, c), c))
    k = min(60, len(brute))
    chains = lightest_logical_chains(graph, k)
    assert len(chains) == k and len(set(chains)) == k
    found = [_weight(graph, c) for c in chains]
    expected = [_weight(graph, c) for c in brute[:k]]
    assert found == pytest.approx(expected, rel=1e-12)
    assert set(chains) <= set(brute)
    every = lightest_logical_chains(graph, 10 * len(brute))
    assert sorted(every) == sorted(brute)  # complete when k exceeds the number of cycles


def test_min_length_cycles_are_among_the_chains_when_k_is_large() -> None:
    graph = _graph(5, 2)
    exact = set(logical_cycles(graph))
    chains = set(lightest_logical_chains(graph, 2000))
    assert exact <= chains


def test_scaled_score_on_exact_cycles_reproduces_the_v2_score() -> None:
    graph = _graph(5, 3)
    exact_cycles = logical_cycles(graph)
    exact = math.fsum(
        [
            graph.undetectable_logical,
            *(cycle_failure_probability([graph.edges[e] for e in c]) for c in exact_cycles),
        ]
    )
    dp = math.fsum(
        [
            graph.undetectable_logical,
            *(chain_failure_probability([graph.edges[e] for e in c], 0.01) for c in exact_cycles),
        ]
    )
    assert dp == pytest.approx(exact, rel=1e-3)


def test_scaled_score_grows_with_k_and_is_deterministic() -> None:
    stored = load_device("hanoi")
    circuit = repetition_code(3, 3, "Z")
    mapping = enumerate_placements(circuit, stored.device)[0]
    scores = [
        scaled_score(circuit, stored.device, stored.noise, mapping, ScaledParameters(k, 0.1))
        for k in (1, 5, 50)
    ]
    assert scores[0] < scores[1] < scores[2]
    again = scaled_score(circuit, stored.device, stored.noise, mapping, ScaledParameters(50, 0.1))
    assert again == scores[2]


def test_empty_graph_scores_its_undetectable_part() -> None:
    graph = MatchingGraph(edges={}, undetectable_logical=0.25, non_graphlike=0)
    assert scaled_score_from_graph(graph, ScaledParameters(10, 0.1)) == 0.25
