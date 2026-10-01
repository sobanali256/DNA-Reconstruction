"""Day 4: does the fallback (ITR) fix the clusters BBS gets wrong, and does BBS confidence find them?

    .venv/bin/python analysis/cascade_from_cache.py configs/pilot_cascade.yaml

Reads one BBS-only run and one fallback run folder (ITR-only, or BBS-only with other
settings; never reruns anything), keeps the clusters
of the configured split, and writes
  <prefix>_cascade.csv    2x2 table (exact match), rescuable share, failure AUROC
                          (long format: section, metric, value, note);
  <prefix>_tau_sweep.csv  the adaptive pipeline per selector and threshold.
In the outputs "fallback" is the second run.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import yaml
from scipy.stats import binomtest

from dnarecon.metrics import cascade_outcome, failure_auroc
from dnarecon.results import read_run

ROOT = Path(__file__).resolve().parent.parent


def load_cache(bbs_dir: Path, fallback_dir: Path, split: str) -> tuple[pd.DataFrame, list[str], str]:
    """One row per cluster: BBS columns from the BBS run, itr_* columns from the fallback run.

    The fallback is an ITR-only run, or a BBS-only run with other settings (e.g. a wider
    beam) whose bbs_* results are mapped onto the itr_* names the cascade functions use.
    """
    (bbs_m, bbs), (fb_m, fb) = [(manifest, pd.DataFrame(map(asdict, records)))
                                for manifest, records, _ in map(read_run, (bbs_dir, fallback_dir))]
    if bbs_m["method"] != "bbs_only" or fb_m["method"] not in ("itr_only", "bbs_only"):
        sys.exit(f"need a bbs_only run and an itr_only or bbs_only fallback run, got "
                 f"{bbs_m['method']} and {fb_m['method']}")
    if set(bbs.cluster_id) != set(fb.cluster_id):
        sys.exit("runs cover different cluster IDs (gate G3.5)")
    if fb_m["method"] == "bbs_only":  # BBS has no per-cluster runtime
        fb = fb.drop(columns=fb.filter(like="itr_").columns).rename(columns=lambda c: c.replace("bbs_", "itr_"))
        fb = fb.assign(itr_failed=fb.itr_status.ne("ok"), itr_runtime_ms=float("nan"))
    df = bbs[["cluster_id", "split", "expected_length", *bbs.filter(like="bbs_")]].merge(
        fb[["cluster_id", *fb.filter(like="itr_")]], on="cluster_id", validate="one_to_one")
    df = df[df.split == split]
    if df.empty:
        sys.exit(f"no {split} clusters in these runs")
    return df, [bbs_m["run_id"], fb_m["run_id"]], fb_m["method"]


def cascade_rows(df: pd.DataFrame, n_resamples: int, seed: int) -> list[dict]:
    bbs_ok, fb_ok = df.bbs_exact_match.astype(bool), df.itr_exact_match.fillna(False).astype(bool)
    bbs_wrong = df[~bbs_ok]
    rescuable = int(fb_ok[~bbs_ok].sum())
    ci = binomtest(rescuable, len(bbs_wrong)).proportion_ci(method="wilson") if len(bbs_wrong) else None
    rows = [
        row("two_by_two", f"bbs_{b}_fallback_{i}", int(((bbs_ok == (b == "right")) & (fb_ok == (i == "right"))).sum()))
        for b in ("right", "wrong") for i in ("right", "wrong")
    ]
    rows += [
        row("two_by_two", "n_empty_clusters", int((df.bbs_status == "empty_cluster").sum()),
            "counted as wrong for both"),
        row("rescue", "n_bbs_wrong", len(bbs_wrong)),
        row("rescue", "rescuable_share", rescuable / len(bbs_wrong) if len(bbs_wrong) else None,
            "fallback exact / BBS wrong; PLAN.md guideline >= 0.15"),
        row("rescue", "rescuable_share_ci95_low", ci.low if ci else None, "Wilson"),
        row("rescue", "rescuable_share_ci95_high", ci.high if ci else None, "Wilson"),
        row("rescue", "fallback_ed_better_on_bbs_wrong", int((bbs_wrong.itr_edit_distance < bbs_wrong.bbs_edit_distance).sum())),
        row("rescue", "fallback_ed_same_on_bbs_wrong", int((bbs_wrong.itr_edit_distance == bbs_wrong.bbs_edit_distance).sum())),
        row("rescue", "fallback_ed_worse_on_bbs_wrong", int((bbs_wrong.itr_edit_distance > bbs_wrong.bbs_edit_distance).sum())),
        row("rescue", "bbs_exact_rate", bbs_ok.mean()),
        row("rescue", "oracle_exact_rate", (bbs_ok | fb_ok).mean(), "either engine exact: ceiling of any selector"),
    ]
    scored = df[df.bbs_status == "ok"]  # clusters with a BBS output (confidence, path weight)
    is_wrong = ~scored.bbs_exact_match.astype(bool)
    rows += [
        row("auroc", "n_clusters", len(scored), "clusters with BBS output; empty clusters excluded"),
        row("auroc", "n_bbs_wrong", int(is_wrong.sum())),
    ]
    if 0 < is_wrong.sum() < len(scored):
        for name, column in (("confidence", "bbs_confidence"), ("path_weight", "bbs_path_weight")):
            auc, low, high = failure_auroc(is_wrong, -scored[column], n_resamples, seed)
            rows += [row("auroc", name, auc, "lower value = predicted failure"),
                     row("auroc", f"{name}_ci95_low", low, f"bootstrap BCa, {n_resamples} resamples"),
                     row("auroc", f"{name}_ci95_high", high)]
    return rows


def row(section, metric, value, note="") -> dict:
    value = round(value, 4) if isinstance(value, float) else value  # numpy floats too
    return {"section": section, "metric": metric, "value": value, "note": note}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", type=Path)
    cfg = yaml.safe_load(parser.parse_args().config.read_text())

    df, run_ids, fallback_method = load_cache(ROOT / cfg["bbs_run"], ROOT / cfg["fallback_run"], cfg["split"])
    summary = pd.DataFrame(cascade_rows(df, cfg["bootstrap"]["n_resamples"], cfg["bootstrap"]["seed"])
                           + [row("runs", "bbs_run", run_ids[0]), row("runs", "fallback_run", run_ids[1], fallback_method),
                              row("runs", "split", cfg["split"])])
    sweep = pd.DataFrame([cascade_outcome(df, tau, length_check)
                          for length_check in (False, True) for tau in cfg["taus"]])
    sweep = sweep.rename(columns={"itr_seconds": "fallback_seconds"})
    if fallback_method == "bbs_only":
        sweep["fallback_seconds"] = float("nan")  # BBS has no per-cluster runtime

    prefix = ROOT / cfg["output_prefix"]
    prefix.parent.mkdir(parents=True, exist_ok=True)
    for table, suffix in ((summary, "cascade"), (sweep, "tau_sweep")):
        path = prefix.with_name(f"{prefix.name}_{suffix}.csv")
        table.to_csv(path, index=False, float_format="%.4f", lineterminator="\n")
        print(table.to_string(index=False, float_format=lambda v: f"{v:.4f}"), f"\nWrote {path}\n")


if __name__ == "__main__":
    main()
