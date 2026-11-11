"""The detection-rate proxy (SPEC_V3.md revision 6, R3-C16; exploratory).

Kan et al. (arXiv:2610.23840) rank surface-code placements by how often detectors fire. The
noise-model version used here needs no sampling: for each detector of the placement's
approximate, undecomposed detector error model, the probability that it fires is
(1 - prod over the errors e that flip it of (1 - 2 p_e)) / 2, the parity of independent
flips; the score is the mean over detectors (lower is better). It sees the same noise as the
decoder-graph score but none of the matching structure, so it asks whether the decoder graph
adds anything beyond "fewer detection events".
"""

from __future__ import annotations

import math

import stim

METHOD = (
    "Mean over detectors of the probability that the detector fires, "
    "(1 - prod_e (1 - 2 p_e)) / 2 over the errors e of the placement's approximate, "
    "undecomposed Stim DEM that flip it (Kan et al.'s detection-rate proxy, computed from the "
    "model instead of sampled)"
)


def detection_rate(dem: stim.DetectorErrorModel) -> float:
    """Mean firing probability of the detectors of ``dem`` (lower is better)."""

    n = int(dem.num_detectors)
    if n == 0:
        raise ValueError("the model has no detectors")
    keep = [1.0] * n
    for instruction in dem.flattened():
        if not isinstance(instruction, stim.DemInstruction) or instruction.type != "error":
            continue
        p = float(instruction.args_copy()[0])
        flipped: set[int] = set()
        for target in instruction.targets_copy():
            if target.is_relative_detector_id():
                flipped ^= {int(target.val)}
        for d in flipped:
            keep[d] *= 1.0 - 2.0 * p
    return math.fsum((1.0 - k) / 2.0 for k in keep) / n


__all__ = ["METHOD", "detection_rate"]
