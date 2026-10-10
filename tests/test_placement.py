"""T-C1, T-C2, T-C3, T-C5, T-C6, T-C8, T-I3, T-I7 and R-D1/R-D2/R-P1-R-P6."""

from __future__ import annotations

from typing import Any

import networkx as nx
import nove
import numpy as np
import pytest
import stim
from conftest import CIRCUITS, circuit_for

from noise_aware_compilation import evaluation
from noise_aware_compilation.conditions import uniform_median
from noise_aware_compilation.devices import StoredDevice
from noise_aware_compilation.placement import (
    TIE_BREAK_RULE,
    NoValidPlacementError,
    enumerate_placements,
    fixed_placement,
    interaction_edges,
    is_valid_placement,
    lowered_circuit,
    place,
    relabel_circuit,
    unrelabel_circuit,
)

IDS = [f"{d}-{c}" for d, c, *_ in CIRCUITS]


@pytest.mark.parametrize(("device", "label", "distance", "basis", "expected"), CIRCUITS, ids=IDS)
def test_t_c3_enumeration_complete(
    device: str,
    label: str,
    distance: int,
    basis: str,
    expected: int,
    devices: dict[str, StoredDevice],
) -> None:
    circuit = circuit_for(distance, basis)
    dev = devices[device].device
    mappings = enumerate_placements(circuit, dev)
    assert len(mappings) == expected
    assert mappings == sorted(mappings) and len(set(mappings)) == len(mappings)
    # independent count: subgraph monomorphisms of the interaction graph (networkx VF2)
    big = nx.Graph()
    big.add_edges_from(dev.connectivity)
    small = nx.Graph()
    small.add_nodes_from(range(circuit.num_qubits))
    small.add_edges_from(interaction_edges(circuit))
    matcher = nx.algorithms.isomorphism.GraphMatcher(big, small)
    found = {
        tuple(inv[c] for c in range(circuit.num_qubits))
        for iso in matcher.subgraph_monomorphisms_iter()
        for inv in [{c: p for p, c in iso.items()}]
    }
    assert found == set(mappings)


@pytest.mark.parametrize(("device", "label", "distance", "basis", "expected"), CIRCUITS, ids=IDS)
def test_t_c1_placement_validity(
    device: str,
    label: str,
    distance: int,
    basis: str,
    expected: int,
    devices: dict[str, StoredDevice],
) -> None:
    circuit = circuit_for(distance, basis)
    dev, noise = devices[device].device, devices[device].noise
    result = place(circuit, dev, noise)
    assert len(result.candidates) == expected  # R-P2
    for cand in result.candidates:
        assert is_valid_placement(circuit, dev, cand.mapping)  # R-P1
        experiment = nove.Experiment(
            relabel_circuit(circuit, cand.mapping),
            noise=nove.NoiseModel(hardware=noise),
            device=dev,
        )
        experiment.analyze_support("stim").require_supported()  # Nove raises no error
        experiment.resolved_circuit()
    assert is_valid_placement(circuit, dev, result.mapping)
    assert result.mapping in {c.mapping for c in result.candidates}
    assert result.score == min(c.score for c in result.candidates)
    assert result.mapping_dict == dict(enumerate(result.mapping))  # circuit qubit -> physical


@pytest.mark.parametrize(("device", "label", "distance", "basis", "expected"), CIRCUITS, ids=IDS)
def test_t_c2_circuit_meaning_preserved(
    device: str,
    label: str,
    distance: int,
    basis: str,
    expected: int,
    devices: dict[str, StoredDevice],
) -> None:
    circuit = circuit_for(distance, basis)
    dev = devices[device].device
    original_text = circuit.to_stim_text()
    for mapping in enumerate_placements(circuit, dev):
        mapped = relabel_circuit(circuit, mapping)
        assert mapped.num_detectors == circuit.num_detectors
        assert mapped.num_observables == circuit.num_observables
        assert mapped.num_measurements == circuit.num_measurements
        # measurement order: the k-th measurement acts on mapping[original target]
        orig_m = [t.value for i in circuit.to_stim() if i.name == "M" for t in i.targets_copy()]
        new_m = [t.value for i in mapped.to_stim() if i.name == "M" for t in i.targets_copy()]
        assert new_m == [mapping[q] for q in orig_m]
        dets, obs = (
            mapped.to_stim()
            .compile_detector_sampler(seed=7)
            .sample(10_000, separate_observables=True)
        )
        assert not dets.any() and not obs.any()
        back = unrelabel_circuit(mapped, mapping, circuit.num_qubits)
        assert back.to_stim_text() == original_text


def test_t_c5_no_valid_placement_raises(devices: dict[str, StoredDevice]) -> None:
    star = stim.Circuit(
        "R 0 1 2 3 4\nTICK\nCX 0 1\nTICK\nCX 0 2\nTICK\nCX 0 3\nTICK\nCX 0 4\nM 0 1 2 3 4"
    )
    circuit = nove.Circuit.from_stim(star)
    for stored in devices.values():
        with pytest.raises(NoValidPlacementError, match="no SWAP-free placement"):
            enumerate_placements(circuit, stored.device)
        with pytest.raises(NoValidPlacementError):
            place(circuit, stored.device, stored.noise)


def test_t_c6_determinism_and_tie_rule(devices: dict[str, StoredDevice]) -> None:
    for name, stored in devices.items():
        circuit = circuit_for(3, "Z")
        a = place(circuit, stored.device, stored.noise)
        b = place(circuit, stored.device, stored.noise)
        assert a == b
        # Uniform noise: every score ties, so the documented rule picks the
        # lexicographically smallest mapping.
        uniform = uniform_median(stored.noise, stored.device)
        tied = place(circuit, stored.device, uniform)
        scores = {round(c.score, 10) for c in tied.candidates}
        assert len(scores) == 1, name
        assert tied.mapping == min(c.mapping for c in tied.candidates)
        assert tied.mapping == fixed_placement(circuit, stored.device)
        assert "lexicographically smallest" in TIE_BREAK_RULE


def test_t_c8_selection_without_sampling(
    monkeypatch: pytest.MonkeyPatch, devices: dict[str, StoredDevice]
) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a sampler was used during selection")

    monkeypatch.setattr(stim.Circuit, "compile_detector_sampler", boom)
    monkeypatch.setattr(stim.Circuit, "compile_sampler", boom)
    monkeypatch.setattr(nove.Experiment, "compile", boom)
    monkeypatch.setattr(nove.Experiment, "run", boom)
    monkeypatch.setattr(nove, "run", boom)
    monkeypatch.setattr(evaluation.PlacementEvaluator, "run_chunk", boom)
    monkeypatch.setattr(evaluation.PlacementEvaluator, "run", boom)
    import pymatching

    monkeypatch.setattr(pymatching.Matching, "decode_batch", boom)
    monkeypatch.setattr(pymatching.Matching, "decode", boom)
    monkeypatch.setattr(np.random, "default_rng", boom)
    for stored in devices.values():
        result = place(circuit_for(3, "Z"), stored.device, stored.noise)
        assert result.candidates
    with pytest.raises(AssertionError, match="sampler"):
        stim.Circuit("M 0").compile_detector_sampler()


def test_t_i3_noise_on_mapped_qubits(devices: dict[str, StoredDevice]) -> None:
    for stored in devices.values():
        circuit = circuit_for(3, "Z")
        dev, noise = stored.device, stored.noise
        for mapping in enumerate_placements(circuit, dev)[:10]:
            lowered = lowered_circuit(circuit, dev, noise, mapping)
            expected_pairs = {
                (mapping[a], mapping[b])
                for inst in circuit.to_stim()
                if inst.name == "CX"
                for a, b in [(t[0].value, t[1].value) for t in inst.target_groups()]
            }
            seen_pairs = set()
            measured: dict[int, float] = {}
            for inst in lowered:
                if inst.name == "PAULI_CHANNEL_2":
                    for g in inst.target_groups():
                        pair = (g[0].value, g[1].value)
                        seen_pairs.add(pair)
                        ch = noise.gate_channel_for(nove.OperationKey("CX", pair))
                        assert ch is not None
                        assert tuple(inst.gate_args_copy()) == pytest.approx(
                            ch.probabilities, rel=1e-12
                        )
                if inst.name == "M":
                    for t in inst.targets_copy():
                        measured[t.value] = inst.gate_args_copy()[0]
            assert seen_pairs == expected_pairs
            assert set(measured) == set(mapping)
            for q, p in measured.items():
                assert p == noise.readout_probability_for(q)


@pytest.mark.parametrize(("distance", "basis"), [(3, "Z"), (3, "X"), (5, "Z"), (7, "Z")])
def test_t_i7_second_device_pipeline(
    distance: int, basis: str, devices: dict[str, StoredDevice]
) -> None:
    stored = devices["hanoi"]
    circuit = circuit_for(distance, basis)
    result = place(circuit, stored.device, stored.noise)
    assert is_valid_placement(circuit, stored.device, result.mapping)
    ev = evaluation.PlacementEvaluator(
        circuit, stored.device, uniform_median(stored.noise, stored.device), result.mapping
    )
    counts = ev.run_chunk(1000, 5)
    assert counts.shots == 1000


def test_relabel_rejects_bad_mapping() -> None:
    circuit = circuit_for(3, "Z")
    with pytest.raises(ValueError):
        relabel_circuit(circuit, (0, 0, 1, 2, 3))
    with pytest.raises(ValueError):
        relabel_circuit(circuit, (0, 1, 2))
