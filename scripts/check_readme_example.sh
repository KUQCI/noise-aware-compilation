#!/usr/bin/env bash
# T2-I5: run the README's worked example as written in a fresh virtual environment.
# The install line is replaced by an install of this checkout, so the check covers the
# code being tested rather than the published branch.
# Usage: bash scripts/check_readme_example.sh [work-dir]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${1:-$(mktemp -d)}"
python3 -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install -q --upgrade pip
cd "$WORK"
python3 - "$ROOT/README.md" > example.sh <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
block = re.search(r"<!-- worked-example:start -->\s*```bash\n(.*?)```", text, re.DOTALL).group(1)
print(block.replace(block.splitlines()[0], 'python -m pip install -q "$ROOT"', 1))
PY
ROOT="$ROOT" PATH="$WORK/venv/bin:$PATH" bash -euo pipefail example.sh
"$WORK/venv/bin/python" -c "import importlib.util as u; assert u.find_spec('qiskit') is None"
echo "T2-I5 ok: README worked example ran in a fresh environment without Qiskit"
