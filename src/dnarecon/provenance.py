"""Reproducibility metadata: git hashes, tool versions, hardware (design doc v3, 4.3)."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[2]
PINS_PATH = ROOT / "scripts" / "external_pins.env"


def read_pins(path: Path = PINS_PATH) -> dict[str, str]:
    """Parse KEY=VALUE lines from scripts/external_pins.env."""
    pins = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            pins[key.strip()] = value.strip()
    return pins


def run_text(cmd: list[str], cwd: Path | None = None) -> str | None:
    """Return stripped stdout of a command, or None if it cannot run."""
    try:
        out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def git_head(repo: Path) -> str | None:
    return run_text(["git", "rev-parse", "HEAD"], cwd=repo)


def git_is_dirty(repo: Path) -> bool | None:
    status = run_text(["git", "status", "--porcelain"], cwd=repo)
    return None if status is None else bool(status)


def cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def hardware_info() -> dict[str, object]:
    return {
        "cpu_model": cpu_model(),
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": psutil.cpu_count(logical=True),
        "usable_cores": len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
        "ram_total_gb": round(psutil.virtual_memory().total / 2**30, 1),
        "os": platform.platform(),
        "wsl": "microsoft" in platform.release().lower(),
    }


def tool_versions() -> dict[str, str | None]:
    first_line = lambda s: s.splitlines()[0] if s else None  # noqa: E731
    return {
        "python": sys.version.split()[0],
        "g++": first_line(run_text(["g++", "--version"])),
        "cargo": run_text(["cargo", "--version"]),
        "rustc": run_text(["rustc", "--version"]),
    }


def provenance() -> dict[str, object]:
    """Everything a run folder needs to record about where and with what it ran."""
    pins = read_pins()
    return {
        "project_commit": git_head(ROOT),
        "project_dirty": git_is_dirty(ROOT),
        "bbs_commit": git_head(ROOT / "external" / "bbs"),
        "bbs_pinned": pins.get("BBS_COMMIT"),
        "itr_commit": git_head(ROOT / "external" / "reconstruction"),
        "itr_pinned": pins.get("ITR_COMMIT"),
        "tools": tool_versions(),
        "hardware": hardware_info(),
    }
