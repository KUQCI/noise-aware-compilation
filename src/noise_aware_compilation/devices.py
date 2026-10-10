"""Converted device snapshots stored in the package (R-D4).

Each stored device is a pair of JSON files written by
``scripts/build_device_snapshots.py``: ``<name>_snapshot.json`` (a Nove
``CalibrationSnapshot``) and ``<name>_device.json`` (a Nove ``DeviceModel``),
plus ``hashes.json`` holding the SHA-256 of both. Loading verifies the hashes.
Nothing here depends on which device is loaded (R-D5).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources

import nove

from noise_aware_compilation.conversion import noise_model_from_snapshot

_PACKAGE = "noise_aware_compilation.device_data"


@dataclass(frozen=True)
class StoredDevice:
    """A device loaded from the package's stored snapshot files."""

    name: str
    snapshot: nove.CalibrationSnapshot
    device: nove.DeviceModel

    @property
    def noise(self) -> nove.HardwareNoiseModel:
        return noise_model_from_snapshot(self.snapshot)


def _read(name: str) -> str:
    return resources.files(_PACKAGE).joinpath(name).read_text(encoding="utf-8")


def stored_hashes() -> dict[str, dict[str, str]]:
    """Return the recorded ``{name: {"snapshot_sha256", "device_sha256"}}`` table."""

    data = json.loads(_read("hashes.json"))
    if not isinstance(data, dict):
        raise TypeError("hashes.json must contain an object")
    return data


def available_devices() -> tuple[str, ...]:
    return tuple(sorted(stored_hashes()))


def load_device(name: str) -> StoredDevice:
    """Load a stored device and verify its recorded SHA-256 hashes."""

    hashes = stored_hashes()
    if name not in hashes:
        raise KeyError(f"no stored device {name!r}; available: {sorted(hashes)}")
    snapshot = nove.CalibrationSnapshot.from_json(_read(f"{name}_snapshot.json"))
    device = nove.DeviceModel.from_json(_read(f"{name}_device.json"))
    if snapshot.sha256 != hashes[name]["snapshot_sha256"]:
        raise ValueError(f"stored snapshot for {name!r} does not match its recorded hash")
    if device.sha256 != hashes[name]["device_sha256"]:
        raise ValueError(f"stored device model for {name!r} does not match its recorded hash")
    return StoredDevice(name=name, snapshot=snapshot, device=device)


__all__ = ["StoredDevice", "available_devices", "load_device", "stored_hashes"]
