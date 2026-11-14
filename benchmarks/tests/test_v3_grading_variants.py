"""The grading-only model variants (R3-C15), on FakeFez only (X16)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, cast

import pytest

from nac_bench.directional import prob_meas1_prep0
from nac_bench.grading_variants import variant_model, variant_names
from nac_bench.run_reset import noise_for
from noise_aware_compilation.devices import load_device

ROOT = Path(__file__).resolve().parent.parent
SPEC = json.loads((ROOT / "configs_v3" / "held_out.json").read_text(encoding="utf-8"))[
    "grading_variants"
]


@pytest.fixture(scope="module")
def fez() -> tuple[Any, Any]:
    stored = load_device("fez")
    return stored, noise_for("readout", stored)


def test_variant_names_follow_kind_and_basis() -> None:
    z = variant_names(SPEC, "repetition", "Z")
    assert "directional" in z and "lognormal-0.25-draw3" in z and "cz-without-1q" in z
    assert "directional" not in variant_names(SPEC, "repetition", "X")
    assert "directional" not in variant_names(SPEC, "surface", None)
    assert len(z) == len(variant_names(SPEC, "repetition", "X")) + 1


def test_cz_duration_ends(fez: tuple[Any, Any]) -> None:
    stored, base = fez
    meta = stored.device.metadata
    for which in ("min", "max"):
        device, noise = variant_model(stored, "fez", base, f"cz-duration-{which}", SPEC, 1)
        durations = cast(dict[str, Any], device.to_dict()["gate_durations"])
        assert durations["CZ"] == float(meta[f"cz_duration_{which}"])
        assert noise is base


def test_idle_z_doubles(fez: tuple[Any, Any]) -> None:
    stored, base = fez
    _device, noise = variant_model(stored, "fez", base, "idle-z-x2", SPEC, 1)
    for q, rate in dict(base.idle_rates).items():
        new = dict(noise.idle_rates)[q]
        assert (new.x, new.y, new.z) == (rate.x, rate.y, 2 * rate.z)


def test_cz_without_1q(fez: tuple[Any, Any]) -> None:
    stored, base = fez
    _device, noise = variant_model(stored, "fez", base, "cz-without-1q", SPEC, 1)
    sx = {
        k.targets[0]: math.fsum(c.probabilities) / 1.5
        for k, c in dict(base.gate_channels).items()
        if k.gate == "H"
    }
    changed = 0
    for key, channel in dict(base.gate_channels).items():
        new = dict(noise.gate_channels)[key]
        if key.gate != "CZ":
            assert new == channel
            continue
        a, b = key.targets
        expected = max(math.fsum(channel.probabilities) / 1.25 - sx[a] - sx[b], 0.0)
        assert math.fsum(new.probabilities) / 1.25 == pytest.approx(expected, abs=1e-15)
        assert len(set(new.probabilities)) == 1
        changed += expected < math.fsum(channel.probabilities) / 1.25
    assert changed > 0


def test_lognormal_draws_are_seeded_and_distinct(fez: tuple[Any, Any]) -> None:
    stored, base = fez
    first = variant_model(stored, "fez", base, "lognormal-0.1-draw0", SPEC, 7)[1]
    again = variant_model(stored, "fez", base, "lognormal-0.1-draw0", SPEC, 7)[1]
    other = variant_model(stored, "fez", base, "lognormal-0.1-draw1", SPEC, 7)[1]
    assert first.gate_channels == again.gate_channels
    assert first.gate_channels != other.gate_channels
    with pytest.raises(ValueError):
        variant_model(stored, "fez", base, "lognormal-0.5-draw0", SPEC, 7)


def test_directional_readout(fez: tuple[Any, Any]) -> None:
    stored, base = fez
    _device, noise = variant_model(stored, "fez", base, "directional", SPEC, 1)
    p10 = prob_meas1_prep0("fez")
    for q, p in dict(noise.readout_flip_probabilities).items():
        assert p == p10[int(q)]
    assert dict(noise.reset_flip_probabilities) == dict(noise.readout_flip_probabilities)
