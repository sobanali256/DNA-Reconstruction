"""Supplementary test tables (added after the freeze, analysis only; audit of 6 Oct 2026).

    .venv/bin/python analysis/supplement_tables.py configs/final/supplement.yaml

Reads finished runs only (never reruns anything) and writes
  <prefix>_vs_full_itr.csv   each live cascade vs full ITR (ITR-only test cache), cluster by
                             cluster: exact rates, discordant counts and exact McNemar p, for
                             the pooled grid, the grid without the homopolymer-bias
                             condition(s) (`bias_conditions`), and every condition;
  <prefix>_itr_cost.csv      controlled per-cluster ITR time per condition from the serial
                             full-ITR timing runs (median over repetitions of the condition
                             mean, plus min/max over repetitions and the per-cluster maximum;
                             share_of_itr_time = median mean x clusters / sum: ~one run's share);
  <prefix>_routed_cost.csv  per live cascade: ITR compute of its routed clusters in the serial
                             full-ITR runs (median over repetitions): an estimate of the
                             cascade's serial ITR time from controlled per-cluster times (the
                             length-check policy was never timed itself);
  <prefix>_bbs_repeats.csv   BBS run-to-run variation over every clean synthetic test run with
                             a BBS stage (cache, final live runs, measured scaling runs): exact
                             count per run, and per run the clusters whose BBS sequence differs
                             from the BBS cache, with their maximum confidence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import yaml

from cascade_from_cache import ROOT
from final_results import mcnemar
from scaling_summary import campaign_runs

COLUMNS = ["cluster_id", "dataset_id", "split", "bbs_sequence", "bbs_confidence", "bbs_exact_match",
           "routed_to_itr", "itr_runtime_ms", "exact_match"]


def read(run: str | Path) -> tuple[dict, pd.DataFrame]:
    """Manifest and per-cluster rows of one complete, clean run (sequences kept as strings)."""
    run = ROOT / run
    manifest = json.loads((run / "manifest.json").read_text())
    if manifest["status"] != "complete" or manifest["provenance"]["project_dirty"]:
        raise SystemExit(f"{run.name}: not a complete run from a clean tree")
    df = pd.read_csv(run / "per_cluster.csv", usecols=COLUMNS, keep_default_na=False, dtype=str)
    return manifest, df.set_index("cluster_id")


def campaign(config: str) -> list[dict]:
    """The measured runs summarize_scaling uses for this campaign config (same selection rule)."""
    camp = yaml.safe_load((ROOT / config).read_text())
    expected = {f"{c['scheduler']}_p{w}": int(camp["repetitions"]) for c in camp["cells"] for w in c["workers"]}
    runs, _, _ = campaign_runs(ROOT / "results", camp["campaign"], None, expected)
    if not runs:
        raise SystemExit(f"{config}: no complete campaign")
    return runs


def vs_full_itr(cfg: dict) -> pd.DataFrame:
    _, itr = read(cfg["itr_run"])
    full_ok = itr.exact_match.eq("True")
    bias = set(cfg["bias_conditions"])
    groups = {"all": itr.index, "without_bias": itr.index[~itr.dataset_id.isin(bias)]}
    groups |= {cond: g.index for cond, g in itr.groupby("dataset_id", sort=True)}
    rows = []
    for method, run in cfg["live"].items():
        manifest, live = read(run)
        if set(live.index) != set(itr.index):
            raise SystemExit(f"{method}: live run and ITR cache cover different clusters")
        live_ok = live.exact_match.eq("True").reindex(itr.index)
        for group, ids in groups.items():
            a, f = live_ok[ids], full_ok[ids]
            only_cascade, only_full = int((a & ~f).sum()), int((~a & f).sum())
            p, log10_p = mcnemar(only_cascade, only_full)
            rows.append({"method": method, "tau": manifest["tau"], "selector": manifest["selector"],
                         "group": group, "n_clusters": len(ids),
                         "cascade_exact_rate": a.mean(), "full_itr_exact_rate": f.mean(),
                         "difference_pt": 100 * (a.mean() - f.mean()),
                         "cascade_only_exact": only_cascade, "full_itr_only_exact": only_full,
                         "mcnemar_p": p, "mcnemar_log10_p": log10_p})
    return pd.DataFrame(rows)


def serial_itr_runs(cfg: dict) -> list[dict]:
    return [m for m in campaign(cfg["itr_timing_campaign"]) if m["config"]["campaign"]["cell"] == "serial_p1"]


def routed_cost(cfg: dict) -> pd.DataFrame:
    times = [read(m["_dir"].relative_to(ROOT))[1].itr_runtime_ms.astype(float) / 1000 for m in serial_itr_runs(cfg)]
    rows = []
    for method, run in cfg["live"].items():
        manifest, live = read(run)
        routed = live.index[live.routed_to_itr.eq("True")]
        routed_s = pd.Series([t[routed].sum() for t in times])
        total_s = pd.Series([t.sum() for t in times])
        rows.append({"method": method, "tau": manifest["tau"], "selector": manifest["selector"],
                     "n_routed": len(routed), "n_clusters": len(live),
                     "routed_itr_s_median": routed_s.median(), "routed_itr_s_min": routed_s.min(),
                     "routed_itr_s_max": routed_s.max(), "full_itr_s_median": total_s.median(),
                     "share_of_itr_time": (routed_s / total_s).median(), "n_timing_runs": len(times)})
    return pd.DataFrame(rows)


def itr_cost(cfg: dict) -> pd.DataFrame:
    runs = serial_itr_runs(cfg)
    per_run = []
    for m in runs:
        _, df = read(m["_dir"].relative_to(ROOT))
        df = df.assign(ms=df.itr_runtime_ms.astype(float))
        per_run.append(df.groupby("dataset_id").ms.agg(["mean", "max", "size"]).assign(run_id=m["run_id"]))
    allr = pd.concat(per_run)
    out = allr.groupby(level=0).agg(n_clusters=("size", "first"), n_runs=("run_id", "nunique"),
                                    mean_ms_median=("mean", "median"), mean_ms_min=("mean", "min"),
                                    mean_ms_max=("mean", "max"), max_cluster_ms=("max", "max"))
    total = out.mean_ms_median * out.n_clusters
    return out.assign(share_of_itr_time=total / total.sum()).reset_index(names="dataset_id")


def bbs_repeats(cfg: dict) -> pd.DataFrame:
    _, ref = read(cfg["bbs_run"])
    runs = [(cfg["bbs_run"], "cache")] + [(r, "final") for r in cfg["live"].values()]
    runs += [(m["_dir"].relative_to(ROOT), "scaling") for m in campaign(cfg["bbs_campaign"])]
    rows = []
    for run, kind in runs:
        manifest, df = read(run)
        df = df.reindex(ref.index)
        differs = df.bbs_sequence != ref.bbs_sequence
        conf = df.bbs_confidence.astype(float)
        if not conf.equals(ref.bbs_confidence.astype(float)):
            raise SystemExit(f"{manifest['run_id']}: BBS confidence differs from the cache (not a tie effect)")
        rows.append({"run_id": manifest["run_id"], "kind": kind, "n_clusters": len(df),
                     "bbs_exact": int(df.bbs_exact_match.eq("True").sum()),
                     "bbs_exact_rate": df.bbs_exact_match.eq("True").mean(),
                     "sequence_differs_from_cache": int(differs.sum()),
                     "max_confidence_where_differs": conf[differs].max() if differs.any() else None})
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", type=Path)
    cfg = yaml.safe_load(parser.parse_args().config.read_text())
    prefix = ROOT / cfg["output_prefix"]
    prefix.parent.mkdir(parents=True, exist_ok=True)
    for name, table in (("vs_full_itr", vs_full_itr(cfg)), ("itr_cost", itr_cost(cfg)), ("routed_cost", routed_cost(cfg)),
                        ("bbs_repeats", bbs_repeats(cfg))):
        path = prefix.with_name(f"{prefix.name}_{name}.csv")
        table.to_csv(path, index=False, float_format="%.6g", lineterminator="\n")
        print(table.to_string(index=False, float_format=lambda v: f"{v:.4g}"), f"\nWrote {path}\n")


if __name__ == "__main__":
    main()
