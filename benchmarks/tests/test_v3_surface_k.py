"""R3-S2 (revision 4): the surface code's k by convergence on FakeMiami."""

from __future__ import annotations

from typing import Any

from nac_bench import surface_k_convergence as conv
from noise_aware_compilation.decoder_graph import matching_graph
from noise_aware_compilation.placement import approximate_dem, enumerate_placements, lowered_circuit
from noise_aware_compilation.scaled_score import (
    ScaledParameters,
    chain_failure_probability,
    lightest_logical_chains,
    scaled_score_from_graph,
)
from noise_aware_compilation.surface_code import DEFAULT_SCHEDULE


def test_prefix_score_is_the_scaled_score() -> None:
    device, noise = conv._models("miami", False, "readout")
    circuit = conv._circuit(3, DEFAULT_SCHEDULE)
    mapping = next(iter(enumerate_placements(circuit, device)))
    dem = approximate_dem(lowered_circuit(circuit, device, noise, mapping), decompose=True)
    graph = matching_graph(dem)
    chains = lightest_logical_chains(graph, 40)
    terms = [chain_failure_probability([graph.edges[e] for e in c], 0.05) for c in chains]
    for k in (1, 10, 20, 40):
        direct = scaled_score_from_graph(graph, ScaledParameters(k, 0.05))
        assert conv.prefix_score(graph.undetectable_logical, terms, k) == direct


def test_restored_device_follows_the_reset_setting() -> None:
    _device, readout = conv._models("miami", True, "readout")
    _device, zero = conv._models("miami", True, "zero")
    assert readout.gate_channels == zero.gate_channels
    assert readout.reset_flip_probabilities != zero.reset_flip_probabilities


def _case(rho: dict[int, float], same: dict[int, bool]) -> dict[str, Any]:
    return {"per_k": {str(k): {"rho_k_2k": rho[k], "same_top": same[k]} for k in conv.K_VALUES}}


def test_converged_k_rule() -> None:
    ks = conv.K_VALUES
    rho = {k: 0.95 + 0.01 * i for i, k in enumerate(ks)}  # 0.95 .. 1.01
    same = dict.fromkeys(ks, True)
    chosen = conv.converged_k([_case(rho, same)])
    assert chosen["criterion_met"] and chosen["k"] == ks[4]  # first rho >= 0.99
    # a top-choice change at that k defers to the next one
    chosen = conv.converged_k([_case(rho, same), _case(rho, {**same, ks[4]: False})])
    assert chosen["criterion_met"] and chosen["k"] == ks[5]
    # nothing converges: largest minimum rho, ties to the smaller k
    flat = {k: 0.9 for k in ks} | {ks[2]: 0.95, ks[5]: 0.95}
    chosen = conv.converged_k([_case(flat, same)])
    assert not chosen["criterion_met"] and chosen["k"] == ks[2]
