"""Summarize a scaling campaign: makespan, speedup, efficiency, utilization and imbalance per cell.

    .venv/bin/python analysis/scaling_summary.py configs/scaling_smoke.yaml

Finds the campaign's run folders by the `campaign` block in their manifests (measured,
complete runs only; warm-ups and failed runs are listed but never used). Checks that every
run had the same clusters and the same routed set (gate G8.3) and that every cell has all
its repetitions (G8.1). Per run (from timing.csv and the manifest):
  * makespan       = the run row: wall time of the whole adaptive run (BBS + route + ITR);
  * bbs / itr      = the stage rows' wall times;
  * utilization    = sum of ITR task wall times / (workers x ITR stage wall time);
  * imbalance      = max / mean busy time over the cell's workers (idle workers count as 0).
Per cell: median, IQR and min/max over repetitions; speedup S = median T1 / median Tp, with
T1 = the serial cell (1 worker), for the whole run and for the ITR stage; efficiency = S / p.
Cells with more workers than physical cores are flagged hyper-threaded; never pool them.
Writes <prefix>_runs.csv and <prefix>_cells.csv.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
T1_CELL = "serial_p1"


def campaign_runs(output_root: Path, name: str) -> tuple[list[dict], list[str]]:
    """(manifests of measured complete runs, IDs of every other campaign folder)."""
    used, skipped = [], []
    for path in sorted(output_root.glob("*/manifest.json")):
        m = json.loads(path.read_text())
        if (m.get("config", {}).get("campaign") or {}).get("name") != name:
            continue
        if m.get("status") == "complete" and m.get("measured", True) and m["config"]["campaign"].get("measured", True):
            used.append({**m, "_dir": path.parent})
        else:
            skipped.append(f"{m['run_id']} ({m.get('status')}, measured={m.get('measured')})")
    return used, skipped


def run_row(m: dict) -> dict:
    timing = pd.read_csv(m["_dir"] / "timing.csv")
    stage = timing[timing.kind == "stage"].set_index("id").wall_time_ms
    tasks = timing[timing.kind == "itr_task"]
    sched = m["scheduler"]
    p = sched["workers"]
    busy = [sched["busy_ms"].get(str(w), 0.0) for w in range(p)]
    per_cluster = pd.read_csv(m["_dir"] / "per_cluster.csv", usecols=["routed_to_itr", "itr_status", "status"])
    itr_s = stage.get("itr", 0) / 1000
    return {
        "run_id": m["run_id"], "cell": m["config"]["campaign"]["cell"], "scheduler": sched["mode"],
        "workers": p, "hyperthreaded": sched.get("hyperthreaded", False),
        "repetition": m["config"]["campaign"]["repetition"], "position": m["config"]["campaign"]["position"],
        "n_clusters": m["n_clusters"], "n_routed": m["n_routed"], "routed_ids_sha256": m["routed_ids_sha256"],
        "n_tasks": sched["n_tasks"], "microbatch_size": sched["microbatch_size"],
        "peak_concurrency": sched["peak_concurrency"],
        "makespan_s": timing.loc[timing.kind == "run", "wall_time_ms"].iloc[0] / 1000,
        "bbs_s": stage.get("bbs", 0) / 1000, "route_s": stage.get("route", 0) / 1000, "itr_s": itr_s,
        "itr_task_sum_s": tasks.wall_time_ms.sum() / 1000,
        "utilization": tasks.wall_time_ms.sum() / 1000 / (p * itr_s) if itr_s else None,
        "imbalance": max(busy) / (sum(busy) / p) if sum(busy) else None,
        "itr_timeouts": int((per_cluster.itr_status == "timeout").sum()),
        "itr_failed_tasks": int((tasks.status == "failed").sum()),
        "failed_clusters": int((per_cluster.status == "failed").sum()),
    }


def cell_rows(runs: pd.DataFrame) -> pd.DataFrame:
    def stats(s: pd.Series, prefix: str) -> dict:
        return {f"{prefix}_median": s.median(), f"{prefix}_q1": s.quantile(0.25),
                f"{prefix}_q3": s.quantile(0.75), f"{prefix}_min": s.min(), f"{prefix}_max": s.max()}

    t1 = runs[runs.cell == T1_CELL]
    t1_run, t1_itr = (t1.makespan_s.median(), t1.itr_s.median()) if len(t1) else (None, None)
    rows = []
    for cell, g in runs.groupby("cell", sort=False):
        p = int(g.workers.iloc[0])
        row = {"cell": cell, "scheduler": g.scheduler.iloc[0], "workers": p,
               "hyperthreaded": bool(g.hyperthreaded.iloc[0]), "n_runs": len(g),
               **stats(g.makespan_s, "makespan_s"), **stats(g.itr_s, "itr_s"), "bbs_s_median": g.bbs_s.median(),
               "utilization_median": g.utilization.median(), "imbalance_median": g.imbalance.median(),
               "peak_concurrency_max": int(g.peak_concurrency.max()), "itr_timeouts": int(g.itr_timeouts.sum())}
        if t1_run:
            row.update(speedup=t1_run / row["makespan_s_median"], itr_speedup=t1_itr / row["itr_s_median"])
            row.update(efficiency=row["speedup"] / p, itr_efficiency=row["itr_speedup"] / p)
        rows.append(row)
    order = {"serial": 0, "dynamic": 1, "static": 2, "static_lpt": 3}
    return pd.DataFrame(rows).sort_values(["scheduler", "workers"], key=lambda s: s.map(order) if s.name == "scheduler" else s)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", type=Path)
    camp = yaml.safe_load(parser.parse_args().config.read_text())
    base = yaml.safe_load((ROOT / camp["base"]).read_text())
    used, skipped = campaign_runs(ROOT / base["output_root"], camp["campaign"])
    if not used:
        sys.exit(f"no complete measured runs for campaign {camp['campaign']}")
    for s in skipped:
        print(f"not used: {s}")
    runs = pd.DataFrame([run_row(m) for m in used]).sort_values(["repetition", "position"])
    for key in ("n_clusters", "routed_ids_sha256"):  # G8.3: T1 and Tp on the identical workload
        if runs[key].nunique() != 1:
            sys.exit(f"runs differ in {key}: not one workload")
    expected = {f"{c['scheduler']}_p{w}" for c in camp["cells"] for w in c["workers"]}
    counts = runs.cell.value_counts()
    incomplete = {c: int(counts.get(c, 0)) for c in expected if counts.get(c, 0) != camp["repetitions"]}
    if incomplete:  # G8.1: report, never silently average an incomplete cell
        print(f"WARNING: cells without {camp['repetitions']} repetitions: {incomplete}")
    cells = cell_rows(runs)
    prefix = ROOT / camp["output_prefix"]
    prefix.parent.mkdir(parents=True, exist_ok=True)
    for table, suffix in ((runs, "runs"), (cells, "cells")):
        path = prefix.with_name(f"{prefix.name}_{suffix}.csv")
        table.to_csv(path, index=False, float_format="%.4f", lineterminator="\n")
    show = ["cell", "hyperthreaded", "n_runs", "makespan_s_median", "makespan_s_min", "makespan_s_max",
            "itr_s_median", "bbs_s_median", "speedup", "efficiency", "itr_speedup", "utilization_median",
            "imbalance_median", "peak_concurrency_max", "itr_timeouts"]
    print(cells[[c for c in show if c in cells]].to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    print(f"\n{runs.n_routed.iloc[0]} of {runs.n_clusters.iloc[0]} clusters routed to ITR in every run. "
          f"Wrote {prefix.name}_runs.csv and {prefix.name}_cells.csv")


if __name__ == "__main__":
    main()
