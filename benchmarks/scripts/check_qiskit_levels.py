"""Which pass sets Qiskit's default layout at levels 1 to 3 on the development devices.

Usage::

    python scripts/check_qiskit_levels.py fez marrakesh miami

Prints, for circuits A, B, D5 and D7 at both reset settings, the layout pass and the mapping
Qiskit's default transpile gives at each level, through the same call the held-out runner
uses (``baselines_v3.qiskit_default``). Development devices only (SPEC_V3.md revision 4,
DECISIONS_V3.md P2-26).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nac_bench.baselines_v3 import native_target, qiskit_default
from nac_bench.qiskit_layout import qiskit_circuit
from nac_bench.run_v3 import _circuit
from noise_aware_compilation.devices import load_device
from noise_aware_compilation.placement import enumerate_placements, interaction_edges

SEED = 20261108
KEYS = {
    "A": ("repetition", 3, 3, "Z"),
    "B": ("repetition", 3, 3, "X"),
    "D5": ("repetition", 5, 3, "Z"),
    "D7": ("repetition", 7, 3, "Z"),
}


def main(devices: list[str]) -> None:
    for name in devices:
        stored = load_device(name)
        for reset in ("readout", "none"):
            target = native_target(stored.snapshot, stored.device, reset)
            for label, key in KEYS.items():
                circuit = _circuit(key)
                edges = sorted(interaction_edges(circuit))
                valid = set(enumerate_placements(circuit, stored.device))
                cells = []
                for level in (1, 2, 3):
                    c = qiskit_default(qiskit_circuit(circuit), target, level, SEED, edges, valid)
                    mapping = list(c.mapping) if c.mapping else None
                    cells.append(f"L{level}:{c.layout_pass}:{mapping}")
                print(name, reset, label, " | ".join(cells), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
