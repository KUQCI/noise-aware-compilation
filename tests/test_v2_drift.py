"""v2 Part A drift model: T2-S1 (model level), T2-S2, T2-S3 and R-S2 caps."""

from __future__ import annotations

import math
import statistics

import pytest
from conftest import circuit_for

from noise_aware_compilation.baselines import lowest_summed_error
from noise_aware_compilation.conversion import noise_model_from_snapshot
from noise_aware_compilation.decoder_graph import DECODER_GRAPH_SCORER
from noise_aware_compilation.devices import StoredDevice
from noise_aware_compilation.drift import (
    FLIP_CAP,
    channel_ratios,
    draw_seed,
    drift_snapshot,
)
from noise_aware_compilation.placement import place

DELTAS = (0.1, 0.25, 0.5)


@pytest.mark.parametrize("device", ["jakarta", "hanoi"])
def test_t2_s1_zero_drift_is_the_true_model(device: str, devices: dict[str, StoredDevice]) -> None:
    stored = devices[device]
    for draw in range(3):
        seen = drift_snapshot(stored.snapshot, 0.0, draw_seed(device, draw), draw=draw)
        assert seen.snapshot.sha256 == stored.snapshot.sha256
        assert seen.snapshot.to_json() == stored.snapshot.to_json()
    circuit = circuit_for(3, "Z")
    noise = noise_model_from_snapshot(seen.snapshot)
    for scorer in (None, DECODER_GRAPH_SCORER):
        args = () if scorer is None else (scorer,)
        assert (
            place(circuit, stored.device, noise, *args).mapping
            == place(circuit, stored.device, stored.noise, *args).mapping
        )
    assert (
        lowest_summed_error(circuit, stored.device, noise).mapping
        == lowest_summed_error(circuit, stored.device, stored.noise).mapping
    )


@pytest.mark.parametrize("device", ["jakarta", "hanoi"])
def test_t2_s2_drift_distribution(device: str, devices: dict[str, StoredDevice]) -> None:
    true = devices[device].snapshot
    per_draw = len(channel_ratios(true, true))  # channels with a non-zero true value
    draws = -(-10_000 // per_draw) + 1
    for delta in DELTAS:
        logs: list[float] = []
        for k in range(draws):
            seen = drift_snapshot(true, delta, draw_seed(device, k), draw=k)
            assert not seen.capped  # no cap acts on these devices at these strengths
            noise_model_from_snapshot(seen.snapshot)  # every channel valid (Nove checks)
            logs.extend(math.log(r) for r in channel_ratios(true, seen.snapshot))
        assert len(logs) >= 10_000
        assert abs(statistics.pstdev(logs) / delta - 1) <= 0.05


def test_t2_s3_same_seed_bit_identical(devices: dict[str, StoredDevice]) -> None:
    true = devices["hanoi"].snapshot
    a = drift_snapshot(true, 0.25, 12345)
    b = drift_snapshot(true, 0.25, 12345)
    c = drift_snapshot(true, 0.25, 12346)
    assert a.snapshot.to_json() == b.snapshot.to_json() and a.factors == b.factors
    assert a.snapshot.sha256 != c.snapshot.sha256


def test_r_s2_one_factor_per_channel_and_caps(devices: dict[str, StoredDevice]) -> None:
    true = devices["jakarta"].snapshot
    seen = drift_snapshot(true, 0.5, 7)
    for key, params in true.operation_parameters.items():
        new = seen.snapshot.operation_parameters[key]
        ratios = {new[k] / v for k, v in params.items() if k.startswith("p_") and v > 0}
        assert max(ratios) - min(ratios) <= 1e-12 * max(ratios)  # one factor per channel
        assert all(new[k] == v for k, v in params.items() if k.startswith("source_"))
    for q, params in true.qubit_parameters.items():
        new = seen.snapshot.qubit_parameters[q]
        x, y = (
            new["idle_x_rate"] / params["idle_x_rate"],
            new["idle_y_rate"] / params["idle_y_rate"],
        )
        assert x == pytest.approx(y, rel=1e-12)
    huge = drift_snapshot(true, 50.0, 7)  # forces caps
    assert huge.capped
    noise_model_from_snapshot(huge.snapshot)
    for params in huge.snapshot.qubit_parameters.values():
        assert params["readout_flip_probability"] <= FLIP_CAP
    for params in huge.snapshot.operation_parameters.values():
        assert math.fsum(v for k, v in params.items() if k.startswith("p_")) <= 1 + 1e-12
