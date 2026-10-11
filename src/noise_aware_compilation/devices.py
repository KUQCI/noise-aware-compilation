"""Converted device snapshots stored in the package (R-D4).

Each stored device is a pair of JSON files written by
``scripts/build_device_snapshots.py``: ``<name>_snapshot.json`` (a Nove
``CalibrationSnapshot``) and ``<name>_device.json`` (a Nove ``DeviceModel``),
plus ``hashes.json`` holding the SHA-256 of both. Loading verifies the hashes.
Nothing here depends on which device is loaded (R-D5).

The same three-file layout is what the ``convert`` command writes for a
user's own device (v2 R-I1); :func:`write_device_dir` and
:func:`load_device_dir` read and write it anywhere on disk.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import nove

from noise_aware_compilation.conversion import ConvertedDevice, noise_model_from_snapshot

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


def _verified(
    name: str, snapshot_json: str, device_json: str, recorded: dict[str, str]
) -> StoredDevice:
    snapshot = nove.CalibrationSnapshot.from_json(snapshot_json)
    device = nove.DeviceModel.from_json(device_json)
    if snapshot.sha256 != recorded["snapshot_sha256"]:
        raise ValueError(f"stored snapshot for {name!r} does not match its recorded hash")
    if device.sha256 != recorded["device_sha256"]:
        raise ValueError(f"stored device model for {name!r} does not match its recorded hash")
    return StoredDevice(name=name, snapshot=snapshot, device=device)


def load_device(name: str) -> StoredDevice:
    """Load a stored device and verify its recorded SHA-256 hashes."""

    hashes = stored_hashes()
    if name not in hashes:
        raise KeyError(f"no stored device {name!r}; available: {sorted(hashes)}")
    return _verified(
        name, _read(f"{name}_snapshot.json"), _read(f"{name}_device.json"), hashes[name]
    )


def device_file_names(name: str) -> tuple[str, str]:
    """File names of a stored device's snapshot and device model."""

    return f"{name}_snapshot.json", f"{name}_device.json"


def write_device_dir(converted: ConvertedDevice, out_dir: str | Path, name: str) -> Path:
    """Write a converted device in the stored layout; return the ``hashes.json`` path."""

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    snapshot_file, device_file = device_file_names(name)
    (out / snapshot_file).write_text(converted.snapshot.to_json() + "\n", encoding="utf-8")
    converted.device.write_json(out / device_file)
    hashes = {
        name: {
            "snapshot_sha256": converted.snapshot.sha256,
            "device_sha256": converted.device.sha256,
        }
    }
    path = out / "hashes.json"
    path.write_text(json.dumps(hashes, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_device_dir(path: str | Path) -> StoredDevice:
    """Load a device stored in ``path`` (as written by :func:`write_device_dir`).

    The directory must hold ``hashes.json`` naming exactly one device; its
    recorded hashes are verified.
    """

    directory = Path(path)
    hashes_file = directory / "hashes.json"
    if not hashes_file.is_file():
        raise FileNotFoundError(f"{directory} has no hashes.json; not a stored converted device")
    hashes = json.loads(hashes_file.read_text(encoding="utf-8"))
    if not isinstance(hashes, dict) or len(hashes) != 1:
        raise ValueError(f"{hashes_file} must name exactly one device")
    ((name, recorded),) = hashes.items()
    snapshot_file, device_file = device_file_names(name)
    return _verified(
        name,
        (directory / snapshot_file).read_text(encoding="utf-8"),
        (directory / device_file).read_text(encoding="utf-8"),
        recorded,
    )


__all__ = [
    "StoredDevice",
    "available_devices",
    "device_file_names",
    "load_device",
    "load_device_dir",
    "stored_hashes",
    "write_device_dir",
]
