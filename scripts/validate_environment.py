"""Check that the environment can run the experiments (gate G1.5).

    .venv/bin/python scripts/validate_environment.py

Prints one PASS/FAIL line per check, writes results/env/env_report.json and exits
non-zero if any mandatory check fails. Run it before every experiment batch.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dnarecon.provenance import ROOT, git_head, provenance, read_pins, run_text

REQUIRED_PACKAGES = ["numpy", "pandas", "matplotlib", "scikit-learn", "PyYAML", "edlib", "psutil", "pytest"]
BBS_BIN = ROOT / "external" / "bbs" / "target" / "release" / "bbs"
ITR_UPSTREAM_BIN = ROOT / "external" / "reconstruction" / "Iterative" / "build" / "DNA"
ITR_WRAPPER_BIN = ROOT / "external" / "itr_cli"
MSCNR_DIR = ROOT / "data" / "raw" / "microsoft_cnr"
CHECKSUMS = ROOT / "data" / "splits" / "microsoft_cnr.sha256"
SPLIT_FILE = ROOT / "data" / "splits" / "microsoft_cnr_split.csv"


def check_python() -> tuple[bool, str]:
    ok = sys.version_info >= (3, 11)
    in_venv = sys.prefix != sys.base_prefix
    return ok and in_venv, f"Python {sys.version.split()[0]}, venv={'yes' if in_venv else 'NO'}"


def check_packages() -> tuple[bool, str]:
    missing, found = [], []
    for name in REQUIRED_PACKAGES:
        try:
            found.append(f"{name}={importlib.metadata.version(name)}")
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
    if missing:
        return False, "missing: " + ", ".join(missing)
    return True, ", ".join(found)


def check_tool(cmd: list[str]) -> tuple[bool, str]:
    out = run_text(cmd)
    return out is not None, (out.splitlines()[0] if out else "not found")


def check_pinned(repo: Path, key: str) -> tuple[bool, str]:
    pinned = read_pins()[key]
    actual = git_head(repo)
    if actual is None:
        return False, f"{repo} missing: run scripts/setup_external.sh"
    return actual == pinned, f"{actual[:12]} (pinned {pinned[:12]})"


def check_executable(path: Path, version_cmd: list[str] | None = None) -> tuple[bool, str]:
    if not (path.is_file() and os.access(path, os.X_OK)):
        return False, f"{path} missing or not executable: run scripts/setup_external.sh"
    if version_cmd:
        out = run_text([str(path), *version_cmd])
        return out is not None, out or "failed to run"
    return True, str(path.relative_to(ROOT))


def check_itr_wrapper() -> tuple[bool, str]:
    """The wrapper must build and reconstruct a trivial 3-read cluster."""
    ok, detail = check_executable(ITR_WRAPPER_BIN)
    if not ok:
        return ok, detail
    try:
        out = subprocess.run([str(ITR_WRAPPER_BIN), "--seed", "1"], input=">t 4 3\nACGT\nACGT\nACGA\n",
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"failed to run: {exc}"
    rows = out.stdout.splitlines()
    if out.returncode != 0 or len(rows) != 2 or rows[1].split("\t")[1:2] != ["ok"]:
        return False, f"unexpected output (exit {out.returncode}): {out.stdout!r} {out.stderr!r}"
    return True, detail


def check_dataset() -> tuple[bool, str]:
    if not MSCNR_DIR.is_dir():
        return False, "missing: run scripts/download_microsoft.sh"
    for line in CHECKSUMS.read_text().splitlines():
        expected, name = line.split()
        if hashlib.sha256((MSCNR_DIR / name).read_bytes()).hexdigest() != expected:
            return False, f"checksum mismatch: {name}"
    return True, "Centers.txt, Clusters.txt checksums OK"


def check_split() -> tuple[bool, str]:
    if not SPLIT_FILE.is_file():
        return False, "missing: run scripts/make_split.py"
    n = sum(1 for _ in SPLIT_FILE.open()) - 1
    return n == 10000, f"{n} clusters in {SPLIT_FILE.name}"


def main() -> None:
    checks = {
        "python": check_python(),
        "packages": check_packages(),
        "g++": check_tool(["g++", "--version"]),
        "cargo": check_tool(["cargo", "--version"]),
        "bbs_source": check_pinned(ROOT / "external" / "bbs", "BBS_COMMIT"),
        "itr_source": check_pinned(ROOT / "external" / "reconstruction", "ITR_COMMIT"),
        "bbs_binary": check_executable(BBS_BIN, ["-V"]),
        "itr_upstream_binary": check_executable(ITR_UPSTREAM_BIN),
        "itr_wrapper": check_itr_wrapper(),
        "microsoft_dataset": check_dataset(),
        "microsoft_split": check_split(),
    }

    width = max(map(len, checks))
    for name, (ok, detail) in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")

    info = provenance()
    hw = info["hardware"]
    print(f"\nCPU: {hw['cpu_model']}, {hw['physical_cores']} physical / "
          f"{hw['logical_cores']} logical cores, {hw['ram_total_gb']} GB RAM visible")

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "checks": {name: {"pass": ok, "detail": detail} for name, (ok, detail) in checks.items()},
        "provenance": info,
    }
    out = ROOT / "results" / "env" / "env_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Report: {out.relative_to(ROOT)}")

    failed = [name for name, (ok, _) in checks.items() if not ok]
    if failed:
        sys.exit(f"\nFAILED: {', '.join(failed)}")
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
