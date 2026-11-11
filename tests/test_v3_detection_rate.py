"""The detection-rate proxy (R3-C16) against sampled detection events, on FakeFez only (X16)."""

from __future__ import annotations

import math

import pytest
import stim

from nac_bench.detection_rate import detection_rate
from nac_bench.run_reset import noise_for
from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.devices import load_device
from noise_aware_compilation.fast_lowering import Lowering
from noise_aware_compilation.placement import approximate_dem, enumerate_placements


@pytest.mark.parametrize("distance", [3, 5])
def test_detection_rate_matches_sampling(distance: int) -> None:
    stored = load_device("fez")
    circuit = repetition_code(distance, 3, "Z", "CZ")
    lowering = Lowering(circuit, stored.device, noise_for("readout", stored))
    noisy = lowering.stim_circuit(enumerate_placements(circuit, stored.device)[7])
    computed = detection_rate(approximate_dem(noisy, decompose=False))
    shots = 200_000
    dets = noisy.compile_detector_sampler(seed=20261113).sample(shots)
    sampled = float(dets.mean())
    # shots are independent; detectors within a shot are not, so this bounds the error from
    # the per-shot mean firing rate, whose variance is at most p(1 - p)
    se = math.sqrt(sampled * (1 - sampled) / shots)
    assert abs(computed - sampled) < 4 * se
    assert 0 < computed < 0.5


def test_parity_of_independent_flips() -> None:
    dem = stim.DetectorErrorModel("error(0.1) D0\nerror(0.2) D0 D1\nerror(0.3) D1 D1\n")
    # D0: 0.1 and 0.2 -> (1 - 0.8 x 0.6) / 2; D1: 0.2 only (the 0.3 flips D1 twice)
    expected = ((1 - 0.8 * 0.6) / 2 + 0.2) / 2
    assert detection_rate(dem) == pytest.approx(expected)
    with pytest.raises(ValueError):
        detection_rate(stim.DetectorErrorModel())
