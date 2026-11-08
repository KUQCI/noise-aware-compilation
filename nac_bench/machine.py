"""The machine a run used, for quotable runtimes (roadmap Step 6b.4).

Records CPU model, logical and usable cores, memory, OS and Python, plus a
label the user passes (for example ``patty``). The host name is not recorded.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any


def _cpu_model() -> str:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    if sys.platform == "darwin":
        try:
            return subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            pass
    return platform.processor() or "unknown"


def _memory_bytes() -> int | None:
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    if sys.platform == "darwin":
        try:
            return int(
                subprocess.run(
                    ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True
                ).stdout.strip()
            )
        except (OSError, subprocess.CalledProcessError, ValueError):
            pass
    return None


def _usable_cores() -> int | None:
    sched = getattr(os, "sched_getaffinity", None)
    if sched is not None:
        return len(sched(0))
    return os.cpu_count()


def machine(label: str | None = None, workers: int | None = None) -> dict[str, Any]:
    """A JSON-ready description of this machine and the run's parallelism."""

    memory = _memory_bytes()
    return {
        "label": label,
        "cpu_model": _cpu_model(),
        "logical_cores": os.cpu_count(),
        "usable_cores": _usable_cores(),
        "workers": workers,
        "memory_gib": None if memory is None else round(memory / 2**30, 1),
        "os": platform.platform(),
        "python": platform.python_version(),
        "niceness": os.nice(0) if hasattr(os, "nice") else None,
    }


__all__ = ["machine"]
