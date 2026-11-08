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

``two_qubit_gate="CZ"`` (v3, SPEC_V3.md R3-C2) builds the same code natively
for CZ devices with :func:`cz_native`. For the bit-flip code the result is "H
on the ancilla, CZ to each neighbour, H again"; the phase-flip code keeps its
data in the X basis while they idle. The CZ layers keep the CX circuit's
order and pairs, so a placement uses the same couplers in the same layers.
"""

from __future__ import annotations

from typing import Literal

import nove
import stim

Basis = Literal["Z", "X"]
TwoQubitGate = Literal["CX", "CZ"]


def repetition_code(
    distance: int, rounds: int, basis: Basis = "Z", two_qubit_gate: TwoQubitGate = "CX"
) -> nove.Circuit:
    """Return a noiseless repetition-code memory experiment as a Nove circuit."""

    if two_qubit_gate not in ("CX", "CZ"):
        raise ValueError("two_qubit_gate must be 'CX' or 'CZ'")
    cx = _repetition_code_cx(distance, rounds, basis)
    if two_qubit_gate == "CX":
        return nove.Circuit.from_stim(cx)
    return nove.Circuit.from_stim(cz_native(cx))


def _moments(circuit: stim.Circuit) -> list[list[stim.CircuitInstruction]]:
    moments: list[list[stim.CircuitInstruction]] = [[]]
    for instruction in circuit.flattened():
        if instruction.name == "TICK":
            moments.append([])
        else:
            moments[-1].append(instruction)
    return moments


Moment = list[tuple[str, list[stim.GateTarget], list[float]]]


def _h_qubits(moment: Moment) -> set[int] | None:
    """The qubits of an H-only moment, or None if the moment has any other operation."""

    if not moment or any(name != "H" for name, _, _ in moment):
        return None
    return {t.value for _, targets, _ in moment for t in targets}


def cz_native(circuit: stim.Circuit) -> stim.Circuit:
    """The same circuit built from CZ and H (SPEC_V3.md R3-C2).

    Every CX layer becomes three layers: H on the CX targets, CZ on the same
    pairs, H on the targets. Then, until nothing changes, an H on a qubit in
    one layer and an H on the same qubit in the next layer are both removed
    (H H = I with no time between them), two consecutive H-only layers on
    disjoint qubits are merged (single-qubit gates scheduled as early as
    possible), and empty layers are dropped. An H pair with a non-H layer
    between them stays, so a qubit keeps the basis it idles in.
    """

    moments: list[Moment] = []
    for moment in _moments(circuit):
        cxs = [i for i in moment if i.name in ("CX", "CNOT")]
        if not cxs:
            moments.append([(i.name, i.targets_copy(), i.gate_args_copy()) for i in moment])
            continue
        if len(cxs) != len(moment):
            raise ValueError("a CX layer must contain only CX gates")
        pairs = [t for i in cxs for t in i.targets_copy()]
        targets = [pairs[k + 1] for k in range(0, len(pairs), 2)]
        moments.append([("H", list(targets), [])])
        moments.append([("CZ", pairs, [])])
        moments.append([("H", list(targets), [])])

    changed = True
    while changed:
        changed = False
        # cancel H pairs in consecutive layers
        for m in range(len(moments) - 1):
            a, b = _h_qubits(moments[m]), _h_qubits(moments[m + 1])
            if a is None or b is None:
                continue
            common = a & b
            if common:
                for k in (m, m + 1):
                    moments[k] = [
                        (n, [t for t in ts if t.value not in common], g) for n, ts, g in moments[k]
                    ]
                    moments[k] = [(n, ts, g) for n, ts, g in moments[k] if ts]
                changed = True
        # drop empty layers
        kept = [m for m in moments if m]
        if len(kept) != len(moments):
            moments = kept
            changed = True
        # merge consecutive H-only layers on disjoint qubits
        m = 0
        while m < len(moments) - 1:
            a, b = _h_qubits(moments[m]), _h_qubits(moments[m + 1])
            if a is not None and b is not None and not (a & b):
                targets = [t for _, ts, _ in moments[m] + moments[m + 1] for t in ts]
                moments[m : m + 2] = [[("H", sorted(targets, key=lambda t: t.value), [])]]
                changed = True
            else:
                m += 1

    out = stim.Circuit()
    for m, moment in enumerate(moments):
        if m:
            out.append("TICK")
        for name, targets, args in moment:
            out.append(name, targets, args)
    return out


def _repetition_code_cx(distance: int, rounds: int, basis: Basis) -> stim.Circuit:

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
    return c


__all__ = ["Basis", "TwoQubitGate", "cz_native", "repetition_code"]
