"""T-I6: the linter and type checker pass (the test suite itself is the third check)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True)


@pytest.mark.parametrize(
    "command",
    [
        ("ruff", "check", "."),
        ("ruff", "format", "--check", "."),
    ],
    ids=["ruff-check", "ruff-format"],
)
def test_ruff(command: tuple[str, ...]) -> None:
    exe = shutil.which(command[0]) or str(Path(sys.executable).parent / command[0])
    result = _run(exe, *command[1:])
    assert result.returncode == 0, result.stdout + result.stderr


def test_mypy_strict() -> None:
    result = _run(sys.executable, "-m", "mypy", "--strict")
    assert result.returncode == 0, result.stdout + result.stderr
