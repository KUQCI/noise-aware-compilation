"""Noiseless repetition-code memory circuits (R-F2).

Qubit layout for distance ``d``: circuit qubit ``2i`` is data qubit ``i``
(``0 <= i < d``) and circuit qubit ``2i + 1`` is the ancilla between data
``i`` and ``i + 1``, so the interaction graph is a path ``0-1-...-(2d-2)``.

* ``basis="Z"`` (bit-flip code, circuits A, C, D): data in ``|0...0>``,
  ancillas measure ``Z_i Z_{i+1}`` with ``CX data->ancilla``.
* ``basis="X"`` (phase-flip code, circuit B): circuit A conjugated by H. Data
  are prepared in ``|+...+>`` with ``R`` then ``H``; ancillas measure
  ``X_i X_{i+1}`` as ``H``-``CX ancilla->data``-``H`` and the data are rotated
  back with ``H`` before the final ``M``. The CX direction is therefore the
  reverse of circuit A's (DECISIONS.md D6).

Only ``R``, ``H``, ``CX`` and ``M`` are used, so every operation maps onto a
calibrated Nove channel. Rounds are written out (no ``REPEAT``).
"""

from __future__ import annotations

from typing import Literal

import nove
import stim

Basis = Literal["Z", "X"]


def repetition_code(distance: int, rounds: int, basis: Basis = "Z") -> nove.Circuit:
    """Return a noiseless repetition-code memory experiment as a Nove circuit."""

    if distance < 2:
        raise ValueError("distance must be at least 2")
    if rounds < 1:
        raise ValueError("rounds must be at least 1")
    if basis not in ("Z", "X"):
        raise ValueError("basis must be 'Z' or 'X'")

    data = [2 * i for i in range(distance)]
    ancillas = [2 * i + 1 for i in range(distance - 1)]
    num_anc = len(ancillas)
    c = stim.Circuit()
    c.append("R", data + ancillas)
    c.append("TICK")
    if basis == "X":
        c.append("H", data)
        c.append("TICK")
    for r in range(rounds):
        if r > 0:
            c.append("R", ancillas)
            c.append("TICK")
        if basis == "X":
            c.append("H", ancillas)
            c.append("TICK")
        first = [q for i, a in enumerate(ancillas) for q in (data[i], a)]
        second = [q for i, a in enumerate(ancillas) for q in (data[i + 1], a)]
        if basis == "X":
            first = [q for i, a in enumerate(ancillas) for q in (a, data[i])]
            second = [q for i, a in enumerate(ancillas) for q in (a, data[i + 1])]
        c.append("CX", first)
        c.append("TICK")
        c.append("CX", second)
        c.append("TICK")
        if basis == "X":
            c.append("H", ancillas)
            c.append("TICK")
        c.append("M", ancillas)
        c.append("TICK")
        for i in range(num_anc):
            targets = [stim.target_rec(-num_anc + i)]
            if r > 0:
                targets.append(stim.target_rec(-2 * num_anc + i))
            c.append("DETECTOR", targets, [2 * i + 1, r])
    if basis == "X":
        c.append("H", data)
        c.append("TICK")
    c.append("M", data)
    for i in range(num_anc):
        targets = [
            stim.target_rec(-distance + i),
            stim.target_rec(-distance + i + 1),
            stim.target_rec(-distance - num_anc + i),
        ]
        c.append("DETECTOR", targets, [2 * i + 1, rounds])
    c.append("OBSERVABLE_INCLUDE", [stim.target_rec(-distance)], 0)
    return nove.Circuit.from_stim(c)


__all__ = ["Basis", "repetition_code"]
