"""Nove's hardware-noise lowering, prepared once per circuit (SPEC_V3.md R3-C4).

:func:`noise_aware_compilation.placement.lowered_circuit` relabels the circuit, builds a
:class:`nove.Experiment` and lowers it with :func:`nove.lowering.lower_hardware_noise`,
which appends to Stim one instruction at a time and returns a Nove circuit that is then
turned back into Stim. That overhead is most of the cost of scoring a placement.

For one circuit, device and noise model, only the qubit labels and the probabilities
change from one placement to the next. Nove's durations depend on the gate name alone, so
each moment's length and each circuit qubit's idle time are the same for every placement;
a physical qubit that the placement does not use idles for the whole moment, as Nove
gives every qubit below the highest one used an idle channel. :class:`Lowering` works
the moments out once, caches every channel by its physical qubits, and writes a
placement's circuit as Stim text whose probabilities are ``repr`` floats, which Stim's
parser reads back exactly.

It follows ``lower_hardware_noise`` step for step and gets every probability from the
same model methods (``gate_channel_for``, ``idle_rate_for(...).channel_for_duration``,
``readout_probability_for`` and ``reset_probability_for``), so every probability is the
same float. The tests check that the circuit equals ``lowered_circuit``'s exactly on every
development device, and the v3 runners check a sample of placements in every case. It
refuses what it does not reproduce: crosstalk rules, drift, and targets other than
qubits, inverted qubits and measurement records.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import nove
import stim

from noise_aware_compilation.placement import lowered_circuit

_MEASUREMENTS = {"M", "MX", "MY"}
_MEASURE_RESETS = {"MR", "MRX", "MRY"}
_BASIS_MEASURE = {"MR": "M", "MRX": "MX", "MRY": "MY"}
_BASIS_RESET = {"MR": "R", "MRX": "RX", "MRY": "RY"}
_RESETS = {"R", "RX", "RY"}
_ANNOTATIONS = {"DETECTOR", "OBSERVABLE_INCLUDE", "QUBIT_COORDS", "SHIFT_COORDS"}
_EXPLICIT_NOISE = {
    "X_ERROR",
    "Y_ERROR",
    "Z_ERROR",
    "DEPOLARIZE1",
    "DEPOLARIZE2",
    "PAULI_CHANNEL_1",
    "PAULI_CHANNEL_2",
    "CORRELATED_ERROR",
    "ELSE_CORRELATED_ERROR",
    "E",
}

# A target is ("q", circuit qubit, inverted) or ("rec", offset).
Target = tuple[Any, ...]


class UnsupportedLoweringError(ValueError):
    """The circuit or noise model uses something :class:`Lowering` does not reproduce."""


def _iter_expanded(circuit: stim.Circuit) -> Iterator[stim.CircuitInstruction]:
    for item in circuit:
        if isinstance(item, stim.CircuitRepeatBlock):
            body = item.body_copy()
            for _ in range(item.repeat_count):
                yield from _iter_expanded(body)
        else:
            yield item


def _target(target: stim.GateTarget) -> Target:
    if target.is_qubit_target or target.is_inverted_result_target:
        if target.is_x_target or target.is_y_target or target.is_z_target:
            raise UnsupportedLoweringError(f"Pauli target {target!r}")
        return ("q", int(target.qubit_value), bool(target.is_inverted_result_target))
    if target.is_measurement_record_target:
        return ("rec", int(target.value))
    raise UnsupportedLoweringError(f"target {target!r}")


def _qubit(target: Target) -> int:
    if target[0] != "q":
        raise nove.exceptions.CircuitValidationError(
            f"Expected a physical qubit target, got {target!r}."
        )
    return int(target[1])


def _head(name: str, tag: str, args: Sequence[float]) -> str:
    if any(c in tag for c in "]\\\r\n"):
        raise UnsupportedLoweringError(f"tag {tag!r}")
    text = f"{name}[{tag}]" if tag else name
    if args:
        text += "(" + ", ".join(repr(float(a)) for a in args) + ")"
    return text


def _xor_probability(first: float, second: float) -> float:
    return first + second - 2 * first * second


def _reset_error_name(reset_name: str) -> str:
    return "Z_ERROR" if reset_name in {"RX", "MRX"} else "X_ERROR"


def _channel_line(channel: Any, qubits: tuple[int, ...]) -> str | None:
    if not any(channel.probabilities):
        return None
    name = "PAULI_CHANNEL_1" if channel.arity == 1 else "PAULI_CHANNEL_2"
    return _head(name, "nove:hardware", channel.probabilities) + " " + " ".join(map(str, qubits))


@dataclass(frozen=True)
class _Moment:
    duration: float
    idle: tuple[float, ...]  # by circuit qubit


class Lowering:
    """``lowered_circuit(circuit, device, noise, mapping)`` for many mappings, faster."""

    def __init__(
        self, circuit: nove.Circuit, device: nove.DeviceModel, noise: nove.HardwareNoiseModel
    ) -> None:
        if noise.drift is not None:
            raise UnsupportedLoweringError("drift")
        if noise.crosstalk:
            raise UnsupportedLoweringError("crosstalk rules")
        self.circuit = circuit
        self.device = device
        self.noise = noise
        self.idle_active = bool(noise.idle_rates or noise.default_idle_rate is not None)
        self.gate_active = bool(noise.gate_channels or noise.gate_defaults)
        self.readout_active = bool(
            noise.readout_flip_probabilities or noise.default_readout_flip_probability is not None
        )
        self.reset_active = bool(
            noise.reset_flip_probabilities or noise.default_reset_flip_probability is not None
        )
        # The moments on a device without connectivity: the same durations, and the
        # circuit's own qubit pairs pass the edge check.
        free = nove.DeviceModel.from_dict({**device.to_dict(), "connectivity": []})
        physical = circuit.schedule().physical(free, require_durations=self.idle_active)
        self.moments = [
            _Moment(m.duration, tuple(m.idle_durations[q] for q in range(circuit.num_qubits)))
            for m in physical.moments
        ]
        self.ops: list[tuple[Any, ...]] = []
        self.used: set[int] = set()
        self._compile()
        self._idle: dict[tuple[int, float], str | None] = {}
        self._gate: dict[tuple[str, tuple[int, ...]], str | None] = {}
        self._readout: dict[int, float | None] = {}
        self._reset: dict[int, float | None] = {}

    def _compile(self) -> None:
        tick = 0
        started = False
        ops = self.ops

        def begin() -> None:
            nonlocal started
            if not started:
                ops.append(("IDLE", tick))
                started = True

        for instruction in _iter_expanded(self.circuit.to_stim()):
            name = instruction.name.upper()
            tag = instruction.tag
            if name == "TICK":
                begin()
                ops.append(("TEXT", _head("TICK", tag, ())))
                tick += 1
                started = False
                continue
            begin()
            targets = [_target(t) for t in instruction.targets_copy()]
            for t in targets:
                if t[0] == "q":
                    self.used.add(t[1])
            args = tuple(float(v) for v in instruction.gate_args_copy())
            if name in _MEASUREMENTS | _MEASURE_RESETS:
                supplied = args[0] if args else 0.0
                for t in targets:
                    _qubit(t)
                    ops.append(("MEASURE", _BASIS_MEASURE.get(name, name), tag, t, supplied, name))
                continue
            if name in _RESETS:
                for t in targets:
                    _qubit(t)
                    ops.append(("RESET", _head(name, tag, args), t, name))
                continue
            if name in _ANNOTATIONS | _EXPLICIT_NOISE:
                ops.append(("ASIS", _head(name, tag, args), tuple(targets)))
                continue
            try:
                gate_data = stim.gate_data(name)
            except IndexError:
                ops.append(("ASIS", _head(name, tag, args), tuple(targets)))
                continue
            if not gate_data.is_unitary:
                ops.append(("ASIS", _head(name, tag, args), tuple(targets)))
                continue
            size = 2 if gate_data.is_two_qubit_gate else 1
            if len(targets) % size:
                raise nove.exceptions.CircuitValidationError(f"Invalid target grouping for {name}.")
            head = _head(name, tag, args)
            for i in range(0, len(targets), size):
                group = tuple(_qubit(t) for t in targets[i : i + size])
                ops.append(("GATE", head, name, group))
        begin()

    # Channels, cached by physical qubits. Each is the model's own value.

    def _idle_line(self, qubit: int, duration: float) -> str | None:
        key = (qubit, duration)
        if key not in self._idle:
            rate = self.noise.idle_rate_for(qubit)
            if rate is None:
                raise nove.exceptions.CircuitValidationError(
                    f"No idle rate resolves for qubit {qubit}; configure an exact or default rate."
                )
            self._idle[key] = _channel_line(rate.channel_for_duration(duration), (qubit,))
        return self._idle[key]

    def _gate_line(self, name: str, qubits: tuple[int, ...]) -> str | None:
        key = (name, qubits)
        if key not in self._gate:
            channel = self.noise.gate_channel_for(nove.OperationKey(name, qubits))
            if self.gate_active and channel is None:
                raise nove.exceptions.CircuitValidationError(
                    f"No gate channel resolves for {name}{qubits}; configure an exact or "
                    "named default channel."
                )
            self._gate[key] = None if channel is None else _channel_line(channel, qubits)
        return self._gate[key]

    def _readout_probability(self, qubit: int) -> float | None:
        if qubit not in self._readout:
            p = self.noise.readout_probability_for(qubit)
            if self.readout_active and p is None:
                raise nove.exceptions.CircuitValidationError(
                    f"No readout probability resolves for qubit {qubit}."
                )
            self._readout[qubit] = p
        return self._readout[qubit]

    def _reset_probability(self, qubit: int) -> float | None:
        if qubit not in self._reset:
            p = self.noise.reset_probability_for(qubit)
            if self.reset_active and p is None:
                raise nove.exceptions.CircuitValidationError(
                    f"No reset probability resolves for qubit {qubit}."
                )
            self._reset[qubit] = p
        return self._reset[qubit]

    def _reset_lines(self, lines: list[str], name: str, qubit: int) -> None:
        p = self._reset_probability(qubit)
        if p:
            lines.append(_head(_reset_error_name(name), "nove:reset", (p,)) + f" {qubit}")

    def text(self, mapping: Sequence[int]) -> str:
        """The lowered circuit for ``mapping`` as Stim text."""

        if len(mapping) != self.circuit.num_qubits or len(set(mapping)) != len(mapping):
            raise ValueError("mapping must be one-to-one and cover every circuit qubit")
        width = max(mapping[c] for c in self.used) + 1 if self.used else 0
        inverse = {q: c for c, q in enumerate(mapping)}
        lines: list[str] = []
        for op in self.ops:
            kind = op[0]
            if kind == "TEXT":
                lines.append(op[1])
            elif kind == "IDLE":
                if not self.idle_active:
                    continue
                moment = self.moments[op[1]]
                for q in range(width):
                    c = inverse.get(q)
                    line = self._idle_line(q, moment.duration if c is None else moment.idle[c])
                    if line is not None:
                        lines.append(line)
            elif kind == "GATE":
                _, head, name, group = op
                qubits = tuple(mapping[c] for c in group)
                lines.append(head + " " + " ".join(map(str, qubits)))
                line = self._gate_line(name, qubits)
                if line is not None:
                    lines.append(line)
            elif kind == "MEASURE":
                _, basis, tag, t, supplied, name = op
                q = mapping[t[1]]
                probability = _xor_probability(supplied, self._readout_probability(q) or 0.0)
                lines.append(_head(basis, tag, (probability,)) + (" !" if t[2] else " ") + str(q))
                if name in _MEASURE_RESETS:
                    lines.append(_head(_BASIS_RESET[name], tag, ()) + f" {q}")
                    self._reset_lines(lines, name, q)
            elif kind == "RESET":
                _, head, t, name = op
                q = mapping[t[1]]
                lines.append(head + (" !" if t[2] else " ") + str(q))
                self._reset_lines(lines, name, q)
            else:  # ASIS
                _, head, targets = op
                parts = [head]
                for t in targets:
                    if t[0] == "rec":
                        parts.append(f"rec[{t[1]}]")
                    else:
                        parts.append(("!" if t[2] else "") + str(mapping[t[1]]))
                lines.append(" ".join(parts))
        return "\n".join(lines)

    def stim_circuit(self, mapping: Sequence[int]) -> stim.Circuit:
        """The same circuit as ``lowered_circuit(circuit, device, noise, mapping)``."""

        return stim.Circuit(self.text(mapping))

    def check(self, mapping: Sequence[int]) -> None:
        """Raise unless this placement's circuit equals Nove's lowering exactly."""

        if self.stim_circuit(mapping) != lowered_circuit(
            self.circuit, self.device, self.noise, mapping
        ):
            raise AssertionError(f"fast lowering differs from Nove's for {tuple(mapping)}")


def v1_score_from_dem(dem: stim.DetectorErrorModel) -> float:
    """The v1 score (``placement.score_placement``) of an undecomposed approximate model.

    The same loop as the frozen v1 score, so the same float; ``placement.py`` is frozen by
    v2 (T2-G2) and keeps its own copy.
    """

    total = 0.0
    for instruction in dem.flattened():
        if instruction.type == "error":
            total += float(instruction.args_copy()[0])
    return total


__all__ = ["Lowering", "UnsupportedLoweringError", "v1_score_from_dem"]
