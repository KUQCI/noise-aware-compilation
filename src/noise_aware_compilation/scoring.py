"""Registry of selection scores (v2 R-G3).

Every scorer takes the circuit, device and noise model and a mapping, and
returns a score where lower is better. None samples or simulates (R-P4,
v2 R-G2). The default was set by v2 R-G7 from the held-out FakeAlgiers
results (REPORT_V2.md section 4): the decoder-graph score's mean relative
regret was lower than the v1 score's, with the 95% interval of the difference
below zero, so it is the default. The v1 score stays selectable (R-G3).
``place()`` itself keeps the v1 score as its default, so v1 calls are unchanged.
"""

from __future__ import annotations

from noise_aware_compilation.decoder_graph import DECODER_GRAPH_SCORER
from noise_aware_compilation.placement import V1_SCORER, Scorer

SCORERS: dict[str, Scorer] = {
    V1_SCORER.name: V1_SCORER,
    DECODER_GRAPH_SCORER.name: DECODER_GRAPH_SCORER,
}
DEFAULT_SCORER = DECODER_GRAPH_SCORER.name


def scorer_names() -> tuple[str, ...]:
    return tuple(SCORERS)


def get_scorer(name: str) -> Scorer:
    """Return the scorer called ``name``."""

    try:
        return SCORERS[name]
    except KeyError:
        raise KeyError(
            f"unknown scorer {name!r}; choose one of: {', '.join(scorer_names())}"
        ) from None


__all__ = ["DEFAULT_SCORER", "SCORERS", "get_scorer", "scorer_names"]
