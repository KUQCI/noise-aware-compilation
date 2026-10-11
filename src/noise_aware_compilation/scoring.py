"""Registry of selection scores (v2 R-G3).

Every scorer takes the circuit, device and noise model and a mapping, and
returns a score where lower is better. None samples or simulates (R-P4,
v2 R-G2). The default is the v1 score; v2 R-G7 alone decides whether that
changes.
"""

from __future__ import annotations

from noise_aware_compilation.placement import V1_SCORER, Scorer

SCORERS: dict[str, Scorer] = {V1_SCORER.name: V1_SCORER}
DEFAULT_SCORER = V1_SCORER.name


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
