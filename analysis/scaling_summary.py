"""Summarize a scaling campaign: makespan, speedup, efficiency, utilization and imbalance per cell.

    .venv/bin/python analysis/scaling_summary.py configs/scaling_smoke.yaml

Finds the campaign's run folders by the `campaign` block in their manifests and keeps one
invocation of scripts/run_scaling.py (the latest complete one, or --invocation): measured,
complete runs from a clean tree whose stages all succeeded (a failed BBS shard or ITR task
changes the workload; G8.6). Everything else is listed as not used, with the reason. Stops unless every cell has all its
repetitions (G8.1; --allow-incomplete only to inspect a partial campaign) and every run
used the same code, dataset files, cluster list, clusters and routed set (G8.3).
Per run (from timing.csv and the manifest):
  * makespan       = the run row: wall time of the whole run (adaptive: BBS + route + ITR;
                     itr_only: ITR on every non-empty cluster, "routed" = all of them);
  * bbs / itr      = the stage rows' wall times;
  * utilization    = sum of worker busy times / (workers x ITR stage wall time);
  * imbalance      = max / mean busy time over the cell's workers (idle workers count as 0);
  busy time = the scheduler's per-worker sum of ITR task wall times (manifest busy_ms).
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
sys.path.insert(0, str(ROOT / "scripts"))
from run_scaling import campaign_base  # noqa: E402

T1_CELL = "serial_p1"
SAME_WORKLOAD = ("project_commit", "clusters_file_sha256", "dataset_files_sha256", "n_clusters",
                 "routed_ids_sha256")  # G8.3: T1 and Tp on the identical workload and code


def campaign_runs(output_root: Path, name: str, invocation: str | None,
                  expected: dict[str, int]) -> tuple[list[dict], list[str], str | None]:
    """(usable runs of the chosen invocation, why every other campaign folder is not used, invocation).

    Usable = measured, complete, clean tree, every stage succeeded (timing.csv is read once
    and kept in "_timing"). `expected` maps cell -> repetitions. Without `invocation`, the
    latest launch whose usable runs cover every cell exactly is chosen (an aborted later
    launch cannot hide a complete one); failing that, the latest launch with usable runs.
    """
    found = []
    for path in sorted(output_root.glob("*/manifest.json")):
        m = json.loads(path.read_text())
        camp = (m.get("config") or {}).get("campaign") or {}
        if camp.get("name") == name:
            found.append({**m, "_dir": path.parent, "_camp": camp})
    usable, reasons = {}, {}
    for m in found:
        if m.get("status") != "complete":
            reasons[m["run_id"]] = m.get("status")
        elif not (m.get("measured", True) and m["_camp"].get("measured", True)):
            reasons[m["run_id"]] = "warm-up"
        elif (m.get("provenance") or {}).get("project_dirty", False):
            reasons[m["run_id"]] = "dirty tree: not traceable to a commit"
        else:
            m["_timing"] = pd.read_csv(m["_dir"] / "timing.csv")
            if m["_timing"].loc[m["_timing"].kind == "run", "status"].iloc[0] != "ok":
                reasons[m["run_id"]] = "a stage failed (G8.6)"
            else:
                usable.setdefault(m["_camp"].get("invocation"), []).append(m)

    def complete(runs: list[dict]) -> bool:
        counts: dict[str, int] = {}
        for m in runs:
            counts[m["_camp"]["cell"]] = counts.get(m["_camp"]["cell"], 0) + 1
        return counts == expected

    if invocation is None:
        launches = sorted(usable, key=lambda inv: (inv is not None, inv or ""))  # legacy (None) first
        full = [inv for inv in launches if complete(usable[inv])]
        invocation = full[-1] if full else (launches[-1] if launches else None)
    used = usable.get(invocation, [])
    skipped = [f"{m['run_id']} (invocation {m['_camp'].get('invocation')}: "
               f"{reasons.get(m['run_id'], 'other invocation')})" for m in found if m not in used]
    return used, skipped, invocation


def run_row(m: dict) -> dict:
    timing = m["_timing"]
    stage = timing[timing.kind == "stage"].set_index("id").wall_time_ms
    tasks = timing[timing.kind == "itr_task"]
    sched = m["scheduler"]
    p = sched["workers"]
    busy = [sched["busy_ms"].get(str(w), 0.0) for w in range(p)]
    per_cluster = pd.read_csv(m["_dir"] / "per_cluster.csv", usecols=["routed_to_itr", "itr_status", "status"])
    if m["method"] == "itr_only":  # every non-empty cluster goes to ITR; no routing step
        n_routed, routed_sha = m["n_clusters"] - m["n_empty_clusters"], "itr_only: all non-empty"
    else:
        n_routed, routed_sha = m["n_routed"], m["routed_ids_sha256"]
    itr_s = stage.get("itr", 0) / 1000
    return {
        "run_id": m["run_id"], "cell": m["config"]["campaign"]["cell"], "scheduler": sched["mode"],
        "workers": p, "hyperthreaded": sched.get("hyperthreaded", False),
        "repetition": m["config"]["campaign"]["repetition"], "position": m["config"]["campaign"]["position"],
        "n_clusters": m["n_clusters"], "n_routed": n_routed, "routed_ids_sha256": routed_sha,
        "project_commit": m["provenance"]["project_commit"], "clusters_file_sha256": m["clusters_file_sha256"],
        "dataset_files_sha256": json.dumps(m["dataset_files_sha256"], sort_keys=True),
        "n_tasks": sched["n_tasks"], "microbatch_size": sched["microbatch_size"],
        "peak_concurrency": sched["peak_concurrency"],
        "makespan_s": timing.loc[timing.kind == "run", "wall_time_ms"].iloc[0] / 1000,
        "bbs_s": stage.get("bbs", 0) / 1000, "route_s": stage.get("route", 0) / 1000, "itr_s": itr_s,
        "busy_sum_s": sum(busy) / 1000,
        "utilization": sum(busy) / 1000 / (p * itr_s) if itr_s else None,
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
    parser.add_argument("--invocation", help="run_scaling.py launch to summarize "
                                             "(default: the latest complete one)")
    parser.add_argument("--allow-incomplete", action="store_true",
                        help="inspect a partial campaign (cells without all repetitions); not for reporting")
    args = parser.parse_args()
    camp = yaml.safe_load(args.config.read_text())
    base = campaign_base(camp)  # with overrides: the output_root the runs were written to
    expected = {f"{c['scheduler']}_p{w}": int(camp["repetitions"]) for c in camp["cells"] for w in c["workers"]}
    used, skipped, invocation = campaign_runs(ROOT / base["output_root"], camp["campaign"], args.invocation,
                                              expected)
    for s in skipped:
        print(f"not used: {s}")
    if not used:
        sys.exit(f"no complete measured runs for campaign {camp['campaign']} invocation {invocation}")
    runs = pd.DataFrame([run_row(m) for m in used]).sort_values(["repetition", "position"])
    for key in SAME_WORKLOAD:
        if runs[key].nunique() != 1:
            sys.exit(f"runs differ in {key}: not one workload (G8.3)")
    counts = runs.cell.value_counts()
    incomplete = {c: int(counts.get(c, 0)) for c in set(expected) | set(counts.index)
                  if counts.get(c, 0) != camp["repetitions"]}
    if incomplete:  # G8.1: never average an incomplete campaign into reported numbers
        msg = f"cells without exactly {camp['repetitions']} repetitions: {incomplete}"
        if not args.allow_incomplete:
            sys.exit(f"{msg} (G8.1); rerun the campaign or pass --allow-incomplete to inspect")
        print(f"WARNING: {msg}")
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
    print(f"\nInvocation {invocation}: {runs.n_routed.iloc[0]} of {runs.n_clusters.iloc[0]} clusters routed to ITR in every run. "
          f"Wrote {prefix.name}_runs.csv and {prefix.name}_cells.csv")


if __name__ == "__main__":
    main()
