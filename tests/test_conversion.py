"""T-I1 (converter values), T-I2 (snapshot hash round trip), R-D4 (stored snapshots)."""

from __future__ import annotations

import hashlib
import json
import statistics
from pathlib import Path
from typing import Any

import nove
import pytest

from noise_aware_compilation.conditions import restore_removed_couplers
from noise_aware_compilation.conversion import (
    TWO_QUBIT_LABELS,
    convert_fake_backend,
    cx_probabilities,
)
from noise_aware_compilation.devices import StoredDevice, load_device, stored_hashes

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "devices"


def _raw(name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    conf = json.loads((RAW / f"conf_{name}.json").read_text())
    props = json.loads((RAW / f"props_{name}.json").read_text())
    return conf, props


def _gate(props: dict[str, Any], gate: str, qubits: list[int]) -> dict[str, float]:
    for g in props["gates"]:
        if g["gate"] == gate and g["qubits"] == qubits:
            return {p["name"]: p["value"] for p in g["parameters"]}
    raise KeyError((gate, qubits))


def _qubit(props: dict[str, Any], q: int) -> dict[str, float]:
    return {e["name"]: e["value"] for e in props["qubits"][q]}


def test_raw_file_hashes_match_sha256sums() -> None:
    for line in (RAW / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split()
        assert hashlib.sha256((RAW / name).read_bytes()).hexdigest() == digest


def test_t_i1_jakarta_spec_spot_checks(devices: dict[str, StoredDevice]) -> None:
    snap = devices["jakarta"].snapshot
    for pair in ((4, 5), (5, 4)):  # both CX directions present
        params = snap.operation_parameters[nove.OperationKey("CX", pair)]
        total = sum(params[label] for label in TWO_QUBIT_LABELS)
        assert total == pytest.approx(1.25 * 0.01342, rel=2e-4)
    params = snap.operation_parameters[nove.OperationKey("CX", (4, 5))]
    _, props = _raw("jakarta")
    e = _gate(props, "cx", [4, 5])["gate_error"]
    assert all(params[label] == 1.25 * e / 15 for label in TWO_QUBIT_LABELS)
    assert snap.qubit_parameters[5]["readout_flip_probability"] == pytest.approx(0.0555)


@pytest.mark.parametrize("name", ["jakarta", "hanoi"])
def test_t_i1_every_rule_exactly(name: str, devices: dict[str, StoredDevice]) -> None:
    conf, props = _raw(name)
    snap = devices[name].snapshot
    device = devices[name].device
    noise = devices[name].noise

    # connectivity: every listed pair, except pairs with gate_error > 0.8 in either
    # direction, which are removed in both directions (SPEC.md rev 2)
    def representable(a: int, b: int) -> bool:
        return _gate(props, "cx", [a, b])["gate_error"] <= 0.8

    removed = {
        (min(a, b), max(a, b))
        for a, b in conf["coupling_map"]
        if not (representable(a, b) and representable(b, a))
    }
    listed = {(min(a, b), max(a, b)) for a, b in conf["coupling_map"]}
    assert set(device.connectivity) == listed - removed
    assert {tuple(e["pair"]) for e in device.metadata["removed_couplers"]} == removed
    cx_lengths = []
    for a, b in conf["coupling_map"]:
        g = _gate(props, "cx", [a, b])
        ch = noise.gate_channel_for(nove.OperationKey("CX", (a, b)))
        if (min(a, b), max(a, b)) in removed:
            assert nove.OperationKey("CX", (a, b)) not in snap.operation_parameters
            continue
        cx_lengths.append(g["gate_length"])
        assert ch is not None
        assert ch.probabilities == (1.25 * g["gate_error"] / 15,) * 15
    sx_lengths, ro_lengths, reset_lengths = [], [], []
    for q in range(conf["n_qubits"]):
        sx = _gate(props, "sx", [q])
        sx_lengths.append(sx["gate_length"])
        reset_lengths.append(_gate(props, "reset", [q])["gate_length"])
        h = noise.gate_channel_for(nove.OperationKey("H", (q,)))
        assert h is not None and h.probabilities == (1.5 * sx["gate_error"] / 3,) * 3
        qp = _qubit(props, q)
        ro_lengths.append(qp["readout_length"])
        t1, t2 = qp["T1"] * 1000, qp["T2"] * 1000  # us -> ns
        rate = noise.idle_rate_for(q)
        assert rate is not None
        assert rate.x == rate.y == 1 / (4 * t1)
        assert rate.z == max(1 / (2 * t2) - 1 / (4 * t1), 0.0)  # clamp: DECISIONS.md D4
        assert noise.readout_probability_for(q) == qp["readout_error"]
        assert noise.reset_probability_for(q) == 0.0
    assert device.gate_durations["CX"] == statistics.fmean(cx_lengths)
    assert device.gate_durations["H"] == statistics.fmean(sx_lengths)
    assert device.measurement_duration == statistics.fmean(ro_lengths)
    assert device.reset_duration == statistics.fmean(reset_lengths)
    assert device.time_unit == "ns"
    assert snap.device_id == conf["backend_name"]
    assert snap.captured_at == props["last_update_date"]
    assert snap.provenance["source_package_version"] == "0.50.0"


def test_hanoi_spot_checks(devices: dict[str, StoredDevice]) -> None:
    noise = devices["hanoi"].noise
    for pair in ((12, 13), (13, 12)):
        ch = noise.gate_channel_for(nove.OperationKey("CX", pair))
        assert ch is not None and sum(ch.probabilities) > 0
    _, props = _raw("hanoi")
    assert noise.readout_probability_for(21) == _qubit(props, 21)["readout_error"]


def test_departures_are_recorded(devices: dict[str, StoredDevice]) -> None:
    notes = devices["hanoi"].snapshot.provenance["conversion_notes"]
    assert sum("removed from connectivity" in n for n in notes) == 2
    assert sum("clamped" in n for n in notes) == 5
    assert devices["jakarta"].snapshot.provenance["conversion_notes"] == ()
    removed = devices["hanoi"].device.metadata["removed_couplers"]
    assert sorted(tuple(e["pair"]) for e in removed) == [(5, 8), (19, 20)]
    assert devices["jakarta"].device.metadata["removed_couplers"] == ()


def test_unrepresentable_cx_error_is_rejected() -> None:
    assert cx_probabilities(0.8) == (1.25 * 0.8 / 15,) * 15
    with pytest.raises(ValueError, match="removed"):
        cx_probabilities(0.81)


def test_l1_coupler_better_restores_removed_pairs(devices: dict[str, StoredDevice]) -> None:
    stored = devices["hanoi"]
    device, noise = restore_removed_couplers(stored.device, stored.noise)
    _, props = _raw("hanoi")
    for a, b in ((5, 8), (19, 20)):
        assert device.is_connected(a, b)
        better = min(
            _gate(props, "cx", [a, b])["gate_error"], _gate(props, "cx", [b, a])["gate_error"]
        )
        for pair in ((a, b), (b, a)):
            ch = noise.gate_channel_for(nove.OperationKey("CX", pair))
            assert ch is not None and ch.probabilities == (1.25 * better / 15,) * 15
    assert set(device.connectivity) - set(stored.device.connectivity) == {(5, 8), (19, 20)}
    assert device.gate_durations == stored.device.gate_durations
    jakarta = devices["jakarta"]
    jakarta_noise = jakarta.noise
    same_device, same_noise = restore_removed_couplers(jakarta.device, jakarta_noise)
    assert same_device is jakarta.device and same_noise is jakarta_noise


@pytest.mark.parametrize("name", ["jakarta", "hanoi"])
def test_t_i2_snapshot_json_round_trip(name: str, devices: dict[str, StoredDevice]) -> None:
    snap = devices[name].snapshot
    again = nove.CalibrationSnapshot.from_json(snap.to_json())
    assert again.sha256 == snap.sha256
    dev = devices[name].device
    assert nove.DeviceModel.from_json(dev.to_json()).sha256 == dev.sha256


@pytest.mark.parametrize("name", ["jakarta", "hanoi"])
def test_r_d4_stored_snapshot_matches_fresh_conversion(name: str) -> None:
    fresh = convert_fake_backend(RAW / f"conf_{name}.json", RAW / f"props_{name}.json")
    stored = load_device(name)
    assert (
        fresh.snapshot.sha256 == stored.snapshot.sha256 == stored_hashes()[name]["snapshot_sha256"]
    )
    assert fresh.device.sha256 == stored.device.sha256 == stored_hashes()[name]["device_sha256"]
