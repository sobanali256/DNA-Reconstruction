"""Diagnostic (not a timing result): why does one ITR cluster run slower when others run beside it?

    .venv/bin/python scripts/contention_probe.py

Starts N identical processes at once (N = 1, 2, 4, 8) and records each one's own elapsed
time, for three kinds of work:
  * compute: a pure-Python integer loop. Tiny memory footprint, so it is slowed only by the
    core's clock speed (power/heat limits) or by sharing a core (hyper-threading);
  * memory:  repeated sums over a 256 MB array per process. Slowed also by the shared
    cache and memory bandwidth;
  * itr:     external/itr_cli on the same 3 clusters (coverage 20, synthetic test split).
    Reads only; no ground truth is written.
Slowdown = median per-process time at N / median at N=1; throughput = N / slowdown. If
`compute` slows about as much as `itr` at N=4, the cause is clock speed (power limit); if only
`memory` and `itr` slow, it is shared cache/memory. The N order is rotated per repetition.
Run on the idle laptop (same protocol as the scaling campaign). Writes
results/diagnostics/contention_probe-<stamp>.csv (raw) and prints the summary.
"""

from __future__ import annotations

import argparse
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from dnarecon import provenance as prov  # noqa: E402
from dnarecon.dataset import load_dataset, read_split_table  # noqa: E402
from dnarecon.itr_adapter import write_input  # noqa: E402

KINDS = ("compute", "memory", "itr")
COMPUTE_ITERATIONS = 40_000_000
MEMORY_MB, MEMORY_PASSES = 256, 200
ITR_CLUSTERS = 3


def work(kind: str) -> None:
    """Child process: do one unit of `kind` work and print its own elapsed seconds."""
    if kind == "compute":
        start = time.perf_counter()
        x = 0
        for i in range(COMPUTE_ITERATIONS):
            x = (x + i * i) & 0xFFFF
    else:
        import numpy as np
        data = np.ones(MEMORY_MB * 2**20 // 8)  # allocated before the clock starts
        start = time.perf_counter()
        for _ in range(MEMORY_PASSES):
            data.sum()
    print(time.perf_counter() - start)


def itr_input(path: Path) -> list[str]:
    """Write the ITR probe input (first test clusters of synthetic_e06_c20) and return the command."""
    ds = yaml.safe_load((ROOT / "configs/dataset_synthetic.yaml").read_text())
    splits = read_split_table(ROOT / "data/splits/synthetic_grid_split.csv")
    records = [r for r in load_dataset(ds, ROOT)
               if r.dataset_id == "synthetic_e06_c20" and splits.get(r.cluster_id) == "test"][:ITR_CLUSTERS]
    write_input(records, path)
    itr = yaml.safe_load((ROOT / "configs/itr.yaml").read_text())
    return [str(ROOT / itr["binary"]), "--seed", str(itr["seed"]), str(path)]


def launch(kind: str, n: int, itr_command: list[str]) -> list[float]:
    """Start n copies at once; per-process seconds (ITR: start to exit, measured by polling)."""
    if kind != "itr":
        procs = [subprocess.Popen([sys.executable, __file__, "--worker", kind], stdout=subprocess.PIPE,
                                  text=True) for _ in range(n)]
        out = []
        for p in procs:
            stdout, _ = p.communicate()
            if p.returncode != 0:
                raise RuntimeError(f"{kind} worker failed (exit {p.returncode})")
            out.append(float(stdout.strip()))
        return out
    starts, procs = [], []
    for _ in range(n):
        starts.append(time.perf_counter())
        procs.append(subprocess.Popen(itr_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    ends: dict[int, float] = {}
    while len(ends) < n:
        for i, p in enumerate(procs):
            if i not in ends and p.poll() is not None:
                ends[i] = time.perf_counter()
                if p.returncode != 0:
                    raise RuntimeError(f"itr_cli exit {p.returncode}")
        time.sleep(0.01)
    return [ends[i] - starts[i] for i in range(n)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--worker", choices=("compute", "memory"), help=argparse.SUPPRESS)
    parser.add_argument("--copies", type=int, nargs="+", default=[1, 2, 4, 8])
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if args.worker:
        work(args.worker)
        return
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "results/diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    commit, dirty = prov.git_head(ROOT), prov.git_is_dirty(ROOT)
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        itr_command = itr_input(Path(tmp) / "probe_input.txt")
        for rep in range(1, args.repetitions + 1):
            shift = (rep - 1) % len(args.copies)
            for n in args.copies[shift:] + args.copies[:shift]:
                for kind in KINDS:
                    times = launch(kind, n, itr_command)
                    rows += [{"stamp": stamp, "commit": commit, "dirty": dirty, "repetition": rep,
                              "kind": kind, "copies": n, "process": i, "seconds": t}
                             for i, t in enumerate(times)]
                    print(f"rep {rep} {kind:7s} x{n}: median {statistics.median(times):.2f} s", flush=True)
    raw = pd.DataFrame(rows)
    path = out_dir / f"contention_probe-{stamp}.csv"
    raw.to_csv(path, index=False, float_format="%.4f", lineterminator="\n")
    med = raw.groupby(["kind", "copies"]).seconds.median().unstack("kind")[list(KINDS)]
    table = med / med.loc[1]
    table.columns = [f"{k}_slowdown" for k in KINDS]
    for k in KINDS:
        table[f"{k}_throughput"] = table.index / table[f"{k}_slowdown"]
    print("\nMedian per-process time (s):")
    print(med.to_string(float_format=lambda v: f"{v:.2f}"))
    print("\nSlowdown vs 1 copy, and throughput (work done per unit time vs 1 copy):")
    print(table.to_string(float_format=lambda v: f"{v:.2f}"))
    print(f"\nWrote {path.relative_to(ROOT)}" + ("  (dirty tree)" if dirty else ""))


if __name__ == "__main__":
    main()
