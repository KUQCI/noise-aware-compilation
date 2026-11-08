"""R3-C8 fast search: its parts are right and it stays inside the candidate set."""

from __future__ import annotations

import itertools
import random

import numpy as np
import pytest

from nac_bench.run_reset import noise_for
from noise_aware_compilation.baselines import summed_error
from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.decoder_graph import DECODER_GRAPH_SCORER
from noise_aware_compilation.devices import load_device
from noise_aware_compilation.fast_search import (
    FastSearch,
    NotAPathError,
    SearchParameters,
    path_order,
)
from noise_aware_compilation.placement import (
    enumerate_placements,
    interaction_edges,
    is_valid_placement,
    place,
)
from noise_aware_compilation.surface_code import surface_code

CASES = [("hanoi", 5, "Z", "CX"), ("fez", 5, "X", "CZ"), ("miami", 5, "Z", "CZ")]


def test_path_order_walks_the_interaction_path() -> None:
    circuit = repetition_code(5, 3)
    order = path_order(circuit)
    edges = interaction_edges(circuit)
    assert sorted(order) == list(range(circuit.num_qubits))
    assert all((min(a, b), max(a, b)) in edges for a, b in itertools.pairwise(order))
    with pytest.raises(NotAPathError):
        path_order(surface_code(3, 3))


@pytest.mark.parametrize(("name", "distance", "basis", "gate"), CASES)
def test_summed_error_tables_match_the_baseline(
    name: str, distance: int, basis: str, gate: str
) -> None:
    stored = load_device(name)
    noise = noise_for("readout", stored)
    circuit = repetition_code(distance, 3, basis, two_qubit_gate=gate)  # type: ignore[arg-type]
    placements = enumerate_placements(circuit, stored.device)
    search = FastSearch(circuit, stored.device, noise, placements)
    sample = random.Random(1).sample(placements, 40)
    values = search.tables.values(np.asarray(sample))
    exact = [summed_error(circuit, noise, m) for m in sample]
    assert np.allclose(values, exact, rtol=1e-12, atol=0)


@pytest.mark.parametrize(("name", "distance", "basis", "gate"), CASES)
def test_every_neighbour_is_a_candidate(name: str, distance: int, basis: str, gate: str) -> None:
    stored = load_device(name)
    circuit = repetition_code(distance, 3, basis, two_qubit_gate=gate)  # type: ignore[arg-type]
    placements = enumerate_placements(circuit, stored.device)
    allowed = set(placements)
    search = FastSearch(circuit, stored.device, stored.noise, placements)
    for m in random.Random(2).sample(placements, 30):
        near = search.neighbours(m)
        assert near and len(set(near)) == len(near) and m not in near
        assert all(n in allowed and is_valid_placement(circuit, stored.device, n) for n in near)
        mirror = tuple(m[c] for c in reversed(range(len(m))))
        assert search.neighbours(m)[0] == search._mapping(search._path(m)[::-1])
        assert len(mirror) == len(m)


def test_a_full_prefilter_is_exhaustive_selection() -> None:
    stored = load_device("hanoi")
    circuit = repetition_code(3, 3)
    placements = enumerate_placements(circuit, stored.device)
    search = FastSearch(circuit, stored.device, stored.noise, placements)

    def score(m: tuple[int, ...]) -> float:
        return DECODER_GRAPH_SCORER.function(circuit, stored.device, stored.noise, m)

    result = search.run(score, SearchParameters(prefilter=len(placements), starts=1))
    exhaustive = place(circuit, stored.device, stored.noise, DECODER_GRAPH_SCORER)
    assert result.mapping == exhaustive.mapping
    capped = search.run(score, SearchParameters(prefilter=5, starts=2, max_evaluations=12))
    assert capped.evaluations <= 12
