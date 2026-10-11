"""v2 Part C: the decoder-graph score (R-G1, R-G2, T2-G1)."""

from __future__ import annotations

import math
from typing import Any

import pytest
import stim
from conftest import circuit_for

from noise_aware_compilation import evaluation
from noise_aware_compilation.decoder_graph import (
    BOUNDARY,
    DECODER_GRAPH_SCORER,
    cycle_failure_probability,
    decoder_graph_score_from_dem,
    logical_cycles,
    matching_graph,
)
from noise_aware_compilation.devices import StoredDevice
from noise_aware_compilation.placement import enumerate_placements, is_valid_placement, place
from noise_aware_compilation.scoring import DEFAULT_SCORER, get_scorer


def test_cycle_failure_probability_by_hand() -> None:
    p = 0.01
    # three equal edges: the decoder fails when 2 or 3 flip
    assert cycle_failure_probability([p] * 3) == pytest.approx(3 * p**2 * (1 - p) + p**3)
    # two equal edges: one flip is a tie (counts half), two flips always fail
    assert cycle_failure_probability([p, p]) == pytest.approx(p * (1 - p) + p**2)
    # uneven: one likely edge q and two unlikely edges r. Half the cycle weight lies between
    # w_r and w_q + w_r, so q alone or r alone is corrected, while q with an r, or both r,
    # outweighs the rest and the decoder flips the logical
    q, r = 0.2, 0.001
    w_q, w_r = math.log((1 - q) / q), math.log((1 - r) / r)
    assert w_r < (w_q + 2 * w_r) / 2 < w_q + w_r
    expected = 2 * q * r * (1 - r) + (1 - q) * r * r + q * r * r
    assert cycle_failure_probability([q, r, r]) == pytest.approx(expected, rel=1e-12)


DEM = stim.DetectorErrorModel(
    """
    error(0.1) D0 L0
    error(0.2) D0 D1
    error(0.05) D0 D1
    error(0.3) D1
    error(0.01) L0
    error(0.02) D0 D1 D2
    error(0.04) D2 ^ D1 L0
    """
)


def test_matching_graph_merges_edges_and_finds_undetectable_logicals() -> None:
    graph = matching_graph(DEM)
    assert graph.edges[(0, BOUNDARY, 1)] == pytest.approx(0.1)
    assert graph.edges[(0, 1, 0)] == pytest.approx(0.2 + 0.05 - 2 * 0.2 * 0.05)
    assert graph.edges[(1, BOUNDARY, 0)] == pytest.approx(0.3)
    assert graph.edges[(2, BOUNDARY, 0)] == pytest.approx(0.04)
    assert graph.edges[(1, BOUNDARY, 1)] == pytest.approx(0.04)
    assert graph.undetectable_logical == pytest.approx(0.01)
    assert graph.non_graphlike == 1


def test_logical_cycles_are_shortest_with_odd_observable() -> None:
    graph = matching_graph(DEM)
    cycles = logical_cycles(graph)
    # the shortest logical cycle: D1's two boundary edges, one flipping L0 and one not
    assert cycles == [((1, BOUNDARY, 0), (1, BOUNDARY, 1))]
    expected = 0.01 + cycle_failure_probability([0.3, 0.04])
    assert decoder_graph_score_from_dem(DEM) == pytest.approx(expected)


def test_repetition_code_cycles_have_code_distance(devices: dict[str, StoredDevice]) -> None:
    hanoi = devices["hanoi"]
    for distance in (3, 5, 7):
        circuit = circuit_for(distance, "Z")
        mapping = enumerate_placements(circuit, hanoi.device)[0]
        from noise_aware_compilation.placement import approximate_dem, lowered_circuit

        dem = approximate_dem(
            lowered_circuit(circuit, hanoi.device, hanoi.noise, mapping), decompose=True
        )
        graph = matching_graph(dem)
        assert graph.non_graphlike == 0
        cycles = logical_cycles(graph)
        assert cycles and all(len(c) == distance for c in cycles)


def test_r_g3_scorers_selectable_and_v1_default(devices: dict[str, StoredDevice]) -> None:
    assert DEFAULT_SCORER == "v1"
    assert get_scorer("decoder-graph") is DECODER_GRAPH_SCORER
    jakarta = devices["jakarta"]
    circuit = circuit_for(3, "Z")
    result = place(circuit, jakarta.device, jakarta.noise, DECODER_GRAPH_SCORER)
    assert result.scorer == "decoder-graph"
    assert is_valid_placement(circuit, jakarta.device, result.mapping)
    assert len(result.candidates) == 8


def test_t2_g1_selection_without_sampling(
    monkeypatch: pytest.MonkeyPatch, devices: dict[str, StoredDevice]
) -> None:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("sampling is not allowed during selection")

    for name in ("compile_sampler", "compile_detector_sampler", "compile_m2d_converter"):
        monkeypatch.setattr(stim.Circuit, name, refuse)
    monkeypatch.setattr(evaluation, "PlacementEvaluator", refuse)
    monkeypatch.setattr(evaluation, "simulate", refuse, raising=False)
    import pymatching

    monkeypatch.setattr(pymatching, "Matching", refuse)
    for device, distance, basis in (("jakarta", 3, "X"), ("hanoi", 5, "Z")):
        stored = devices[device]
        result = place(
            circuit_for(distance, basis), stored.device, stored.noise, DECODER_GRAPH_SCORER
        )
        assert result.candidates
