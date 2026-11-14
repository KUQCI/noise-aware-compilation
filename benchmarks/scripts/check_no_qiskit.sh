#!/usr/bin/env bash
# T-I8: install the package into a fresh virtual environment without any extras
# (so without Qiskit) and run one benchmark case there.
# Usage: bash scripts/check_no_qiskit.sh [venv-dir]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${1:-$(mktemp -d)/venv}"
python3 -m venv "$VENV"
"$VENV/bin/pip" install -q "$ROOT"
"$VENV/bin/python" - "$ROOT" <<'PY'
import importlib.util, sys, tempfile
assert importlib.util.find_spec("qiskit") is None, "qiskit is installed"
assert importlib.util.find_spec("qiskit_ibm_runtime") is None, "qiskit-ibm-runtime is installed"
from noise_aware_compilation.benchmark import main
root = sys.argv[1]
out = tempfile.mkdtemp()
assert main([f"{root}/configs/benchmark.json", "--only", "jakarta-A3-N2", "--out", out]) == 0
print("T-I8 ok: package imports and the benchmark runs without Qiskit installed")
PY
