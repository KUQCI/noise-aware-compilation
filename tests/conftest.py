from __future__ import annotations

from pathlib import Path

import pytest

from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.devices import StoredDevice, load_device

ROOT = Path(__file__).resolve().parent.parent

# (device, circuit label, distance, basis, expected valid placements) - T-C3 / SPEC.md rev 2
CIRCUITS = [
    ("jakarta", "A", 3, "Z", 8),
    ("jakarta", "B", 3, "X", 8),
    ("jakarta", "C", 2, "Z", 14),
    ("hanoi", "A", 3, "Z", 80),
    ("hanoi", "B", 3, "X", 80),
    ("hanoi", "D5", 5, "Z", 102),
    ("hanoi", "D7", 7, "Z", 74),
]


@pytest.fixture(scope="session")
def devices() -> dict[str, StoredDevice]:
    return {name: load_device(name) for name in ("jakarta", "hanoi")}


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


def circuit_for(distance: int, basis: str, rounds: int = 3):  # type: ignore[no-untyped-def]
    return repetition_code(distance, rounds, basis)  # type: ignore[arg-type]
