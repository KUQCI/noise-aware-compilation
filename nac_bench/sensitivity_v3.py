"""Correlated-matching re-decode for the v3 held-out sensitivity run (SPEC_V3.md R3-C10).

The family placements of every confirmatory case are decoded a second time with PyMatching's
correlated matching (``enable_correlations=True``, PyMatching 2.3 and later), on exactly the
samples of their main stage: the same experiment, the same chunk seeds and the same shots.
Each chunk is decoded both ways, and the uncorrelated count must equal the main stage's, so
the two decoders are compared on identical syndromes. Exploratory; no claim uses it.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pymatching

from noise_aware_compilation.evaluation import CHUNK_SHOTS, PlacementEvaluator

CORRELATED_DECODER_DESCRIPTION = {
    "decoder": "pymatching, correlated matching",
    "construction": (
        "pymatching.Matching.from_detector_error_model(dem, enable_correlations=True) on the "
        "placement's own approximate, decomposed Stim DEM (the main stage's model); "
        "decode_batch(..., enable_correlations=True) on the main stage's detection events"
    ),
    "pymatching_version": pymatching.__version__,
}


class CorrelatedRedecode:
    """The main stage's samples of one placement, decoded with and without correlations."""

    def __init__(self, evaluator: PlacementEvaluator) -> None:
        self.evaluator = evaluator
        self.correlated = pymatching.Matching.from_detector_error_model(
            evaluator.dem, enable_correlations=True
        )

    def run_chunk(self, shots: int, seed: int) -> tuple[int, int]:
        """(uncorrelated failures, correlated failures) on one main-stage chunk."""

        sampler = self.evaluator.experiment.compile(backend="stim", seed=seed)
        result = sampler.sample(shots)
        dets, observables = result.detection_events, result.observables
        plain = self.evaluator.decoder.decode_batch(dets)
        correlated = self.correlated.decode_batch(dets, enable_correlations=True)
        return (
            int(np.count_nonzero(np.any(plain != observables, axis=1))),
            int(np.count_nonzero(np.any(correlated != observables, axis=1))),
        )

    def run(self, shots: int, seeds: Sequence[int]) -> dict[str, int]:
        needed = -(-shots // CHUNK_SHOTS)
        if len(seeds) < needed:
            raise ValueError(f"need {needed} chunk seeds, got {len(seeds)}")
        plain = correlated = 0
        remaining = shots
        for seed in seeds[:needed]:
            size = min(CHUNK_SHOTS, remaining)
            a, b = self.run_chunk(size, seed)
            plain += a
            correlated += b
            remaining -= size
        return {"shots": shots, "uncorrelated_failures": plain, "correlated_failures": correlated}


__all__ = ["CORRELATED_DECODER_DESCRIPTION", "CorrelatedRedecode"]
