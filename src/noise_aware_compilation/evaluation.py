"""Simulated evaluation of one placement (oracle and baselines only; never selection).

Every placement is evaluated by the same rule (R-F1): relabel the noiseless
circuit, lower the Nove hardware noise model, sample with Nove's ``stim``
backend, and decode with PyMatching built from that placement's own
approximate detector error model (K1).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

import nove
import numpy as np
from nove.decoders.pymatching import PyMatchingDecoder

from noise_aware_compilation.placement import approximate_dem, relabel_circuit

CHUNK_SHOTS = 100_000
DECODER_DESCRIPTION = {
    "decoder": "pymatching",
    "construction": (
        "pymatching.Matching.from_detector_error_model on the placement's own Stim DEM, "
        "built with approximate_disjoint_errors=True, decompose_errors=True, "
        "ignore_decomposition_failures=True; decode_batch on detection events"
    ),
    "decoder_model": "approximate",
    "reason": (
        "K1: Nove's Experiment.to_detector_model() raises for calibrated two-qubit Pauli "
        "channels with more than one non-zero term, so no exact DEM exists; Stim's "
        "approximate_disjoint_errors model is used instead."
    ),
}


@dataclass(frozen=True)
class Counts:
    """Raw counts from simulating one placement with one seed."""

    shots: int
    logical_failures: int
    detector_fires: int
    detector_fires_sq: int
    num_detectors: int

    def __add__(self, other: Counts) -> Counts:
        if self.num_detectors != other.num_detectors:
            raise ValueError("cannot add counts from different circuits")
        return Counts(
            shots=self.shots + other.shots,
            logical_failures=self.logical_failures + other.logical_failures,
            detector_fires=self.detector_fires + other.detector_fires,
            detector_fires_sq=self.detector_fires_sq + other.detector_fires_sq,
            num_detectors=self.num_detectors,
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "shots": self.shots,
            "logical_failures": self.logical_failures,
            "detector_fires": self.detector_fires,
            "detector_fires_sq": self.detector_fires_sq,
            "num_detectors": self.num_detectors,
        }


def derive_seed(*parts: object) -> int:
    """Deterministic 63-bit seed from labelled parts (domain-separated by the first part)."""

    text = "|".join(str(p) for p in parts)
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big") & (2**63 - 1)


class PlacementEvaluator:
    """Compiled experiment and decoder for one placement under one noise model."""

    def __init__(
        self,
        circuit: nove.Circuit,
        device: nove.DeviceModel,
        noise: nove.HardwareNoiseModel,
        mapping: Sequence[int],
    ) -> None:
        self.mapping = tuple(mapping)
        self.experiment = nove.Experiment(
            relabel_circuit(circuit, mapping),
            noise=nove.NoiseModel(hardware=noise),
            device=device,
        )
        noisy = self.experiment.resolved_circuit().to_stim()
        self.dem = approximate_dem(noisy, decompose=True)
        self.decoder = PyMatchingDecoder.from_stim_dem(
            self.dem,
            decoder_model_mismatch=True,
            mismatch_reason="approximate DEM (approximate_disjoint_errors); see K1",
        )
        self.num_detectors = int(noisy.num_detectors)

    def run_chunk(self, shots: int, seed: int) -> Counts:
        sampler = self.experiment.compile(backend="stim", seed=seed)
        result = sampler.sample(shots)
        dets = result.detection_events
        predictions = self.decoder.decode_batch(dets)
        failures = int(np.count_nonzero(np.any(predictions != result.observables, axis=1)))
        per_shot = dets.sum(axis=1, dtype=np.int64)
        return Counts(
            shots=shots,
            logical_failures=failures,
            detector_fires=int(per_shot.sum()),
            detector_fires_sq=int((per_shot * per_shot).sum()),
            num_detectors=self.num_detectors,
        )

    def run(self, shots: int, seeds: Sequence[int]) -> Counts:
        """Run ``shots`` split into chunks of :data:`CHUNK_SHOTS`, one seed per chunk."""

        needed = -(-shots // CHUNK_SHOTS)
        if len(seeds) < needed:
            raise ValueError(f"need {needed} chunk seeds, got {len(seeds)}")
        total: Counts | None = None
        remaining = shots
        for seed in seeds[:needed]:
            size = min(CHUNK_SHOTS, remaining)
            counts = self.run_chunk(size, seed)
            total = counts if total is None else total + counts
            remaining -= size
        assert total is not None
        return total


__all__ = ["CHUNK_SHOTS", "DECODER_DESCRIPTION", "Counts", "PlacementEvaluator", "derive_seed"]
