"""Noise-aware SWAP-free qubit placement for QEC circuits, built on Nove."""

from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.conversion import convert_fake_backend, noise_model_from_snapshot
from noise_aware_compilation.devices import load_device
from noise_aware_compilation.placement import (
    Candidate,
    NoValidPlacementError,
    PlacementResult,
    enumerate_placements,
    fixed_placement,
    place,
    relabel_circuit,
)

__version__ = "0.1.0"

__all__ = [
    "Candidate",
    "NoValidPlacementError",
    "PlacementResult",
    "__version__",
    "convert_fake_backend",
    "enumerate_placements",
    "fixed_placement",
    "load_device",
    "noise_model_from_snapshot",
    "place",
    "relabel_circuit",
    "repetition_code",
]
