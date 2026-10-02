"""Week 3: the headline test table, and the check that the live final runs equal the cache.

    .venv/bin/python analysis/final_results.py configs/final/test_report.yaml

Analysis plan fixed before test was opened (docs/pilot_notes.md, Day 10). Writes
  <prefix>_live_vs_cache.csv  per live final run: the cache prediction at the run's τ and
                              selector vs what the live run did, cluster by cluster
                              (routing, BBS confidence and sequence, ITR output, final
                              sequence, exact-match outcome);
  <prefix>_test_table.csv     one row per (family, group, split, method): exact rate with a
                              95% Wilson CI, mean normalized edit distance (all clusters; no
                              output = 1) and over clusters with output, share routed, and
                              rescued / harmed against BBS only with an exact McNemar p-value
                              (two-sided binomial on the discordant clusters; per-condition
                              p-values are not corrected for multiple comparisons).
Methods: bbs_only and itr_only (cache runs), and one per live run. Test adaptive rows come
from the live runs; dev adaptive rows are cache simulations at the same τ and selector.
Empty clusters stay in every denominator as failures.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy.stats import binomtest

from cascade_from_cache import ROOT, groups, load_cache
from dnarecon.metrics import cascade_outcome, itr_selected
from dnarecon.results import read_run


def simulate(df: pd.DataFrame, tau: float, selector: str) -> pd.DataFrame:
    """Per-cluster cache prediction of the adaptive pipeline (same rules as cascade_outcome)."""
    routed = (df.bbs_confidence < tau) | (df.bbs_status.eq("shard_failed") & (tau > 0))
    has_bbs = df.bbs_status.eq("ok")
    use_itr = itr_selected(routed & df.itr_failed.eq(False), has_bbs, df.itr_sequence.str.len(),
                           df.expected_length, selector).astype(bool)
    out = pd.DataFrame({
        "cluster_id": df.cluster_id,
        "routed": routed.astype(bool),
        "use_itr": use_itr,
        "final_sequence": df.itr_sequence.where(use_itr, df.bbs_sequence),
        "ok": df.itr_exact_match.where(use_itr, df.bbs_exact_match).astype(bool),
        "ned": df.itr_edit_distance.where(use_itr, df.bbs_edit_distance) / df.expected_length,
        "has_output": use_itr | has_bbs,
    })
    expected = cascade_outcome(df, tau, length_check=selector == "length_check")
    if (int(out.ok.sum()), int(out.routed.sum())) != (expected["n_exact"], expected["n_routed"]):
        sys.exit("per-cluster simulation disagrees with metrics.cascade_outcome")
    return out


def baselines(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """BBS only and ITR only as per-cluster frames (ok, ned, has_output, routed)."""
    return {
        "bbs_only": pd.DataFrame({"cluster_id": df.cluster_id, "routed": False,
                                  "ok": df.bbs_exact_match.astype(bool),
                                  "ned": df.bbs_edit_distance / df.expected_length,
                                  "has_output": df.bbs_status.eq("ok")}),
        "itr_only": pd.DataFrame({"cluster_id": df.cluster_id, "routed": True,
                                  "ok": df.itr_exact_match.fillna(False).astype(bool),
                                  "ned": df.itr_edit_distance / df.expected_length,
                                  "has_output": df.itr_failed.eq(False)}),
    }


def load_live(run_dir: Path) -> tuple[dict, pd.DataFrame]:
    manifest, records, _ = read_run(run_dir)
    live = pd.DataFrame(map(asdict, records))
    if manifest["method"] != "adaptive":
        sys.exit(f"{run_dir.name}: not an adaptive run")
    if not np.allclose(live.normalized_edit_distance, live.edit_distance / live.expected_length):
        sys.exit(f"{run_dir.name}: truth length differs from expected_length; cannot normalize the caches")
    return manifest, live


def live_frame(live: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({"cluster_id": live.cluster_id, "routed": live.routed_to_itr.astype(bool),
                         "ok": live.exact_match.astype(bool), "ned": live.normalized_edit_distance,
                         "has_output": live.status.eq("ok")})


def live_vs_cache(name: str, manifest: dict, live: pd.DataFrame, cache: pd.DataFrame) -> list[dict]:
    """Rows comparing one live run with the cache prediction at the same τ and selector."""
    tau, selector = manifest["tau"], manifest["selector"]
    pred = simulate(cache, tau, selector)
    if set(live.cluster_id) != set(cache.cluster_id):
        sys.exit(f"{manifest['run_id']}: live run and cache cover different clusters")
    j = live.merge(cache[["cluster_id", "bbs_confidence", "bbs_sequence", "itr_sequence"]],
                   on="cluster_id", suffixes=("", "_cache"), validate="one_to_one"
                   ).merge(pred, on="cluster_id", suffixes=("", "_pred"), validate="one_to_one")
    routed = j.routed_to_itr.astype(bool)
    bbs_diff = j.bbs_sequence.fillna("") != j.bbs_sequence_cache.fillna("")
    checks = {
        "n_clusters": len(j),
        "tau": tau,
        "selector": selector,
        "n_routed_live": int(routed.sum()),
        "n_routed_cache": int(j.routed.sum()),
        "routing_differs": int((routed != j.routed).sum()),
        "bbs_confidence_differs": int((~np.isclose(j.bbs_confidence, j.bbs_confidence_cache, equal_nan=True)).sum()),
        "bbs_sequence_differs": int(bbs_diff.sum()),
        "bbs_sequence_differs_max_confidence": j.bbs_confidence[bbs_diff].max() if bbs_diff.any() else None,
        "itr_output_differs_on_routed": int((j.itr_sequence.fillna("") != j.itr_sequence_cache.fillna(""))[routed].sum()),
        "final_sequence_differs": int((j.final_sequence.fillna("") != j.final_sequence_pred.fillna("")).sum()),
        "exact_outcome_differs": int((j.exact_match.astype(bool) != j.ok).sum()),
        "n_exact_live": int(j.exact_match.astype(bool).sum()),
        "n_exact_cache": int(j.ok.sum()),
    }
    return [{"method": name, "run_id": manifest["run_id"], "check": k, "value": v} for k, v in checks.items()]


def method_row(m: pd.DataFrame, bbs: pd.DataFrame, method: str, source: str) -> dict:
    n, k = len(m), int(m.ok.sum())
    ci = binomtest(k, n).proportion_ci(method="wilson")
    paired = m.merge(bbs[["cluster_id", "ok"]], on="cluster_id", suffixes=("", "_bbs"), validate="one_to_one")
    rescued = int((paired.ok & ~paired.ok_bbs).sum())
    harmed = int((~paired.ok & paired.ok_bbs).sum())
    p = binomtest(rescued, rescued + harmed).pvalue if method != "bbs_only" and rescued + harmed else None
    return {
        "method": method, "source": source, "n_clusters": n, "n_exact": k,
        "exact_rate": k / n, "exact_ci95_low": ci.low, "exact_ci95_high": ci.high,
        "mean_ned": m.ned.mean(), "n_with_output": int(m.has_output.sum()),
        "mean_ned_with_output": m.ned[m.has_output].mean(),
        "routed_share": m.routed.mean(),
        "rescued_vs_bbs": rescued if method != "bbs_only" else None,
        "harmed_vs_bbs": harmed if method != "bbs_only" else None,
        "mcnemar_p": p,
    }


def family_tables(name: str, fam: dict) -> tuple[list[dict], list[dict]]:
    group_by = fam.get("group_by")
    caches = {split: load_cache(ROOT / fam[split]["bbs_run"], ROOT / fam[split]["itr_run"], split)
              for split in ("dev", "test")}
    for split, (_, _, method) in caches.items():
        if method != "itr_only":
            sys.exit(f"{name} {split}: need a BBS-only and an ITR-only run")
    lives = {method: load_live(ROOT / run) for method, run in fam["live"].items()}

    check_rows = [{"family": name, **r} for method, (man, live) in lives.items()
                  for r in live_vs_cache(method, man, live, caches["test"][0])]

    # per-cluster frames: split -> method -> (frame, source)
    frames: dict[str, dict[str, tuple[pd.DataFrame, str]]] = {}
    for split, (df, _, _) in caches.items():
        frames[split] = {m: (f, "cache") for m, f in baselines(df).items()}
        for method, (man, live) in lives.items():
            frames[split][method] = ((live_frame(live), "live") if split == "test" else
                                     (simulate(df, man["tau"], man["selector"]), "cache simulation"))
    table = []
    for split, (df, _, _) in caches.items():
        for group, g in groups(df, group_by):
            ids = set(g.cluster_id)
            sub = {m: (f[f.cluster_id.isin(ids)], src) for m, (f, src) in frames[split].items()}
            for method, (f, src) in sub.items():
                lives_tau = lives[method][0] if method in lives else None
                table.append({"family": name, "group": group or "all", "split": split,
                              "tau": lives_tau["tau"] if lives_tau else None,
                              "selector": lives_tau["selector"] if lives_tau else None,
                              **method_row(f, sub["bbs_only"][0], method, src)})
    return table, check_rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", type=Path)
    cfg = yaml.safe_load(parser.parse_args().config.read_text())
    table, checks = [], []
    for name, fam in cfg["families"].items():
        t, c = family_tables(name, fam)
        table += t
        checks += c
    prefix = ROOT / cfg["output_prefix"]
    prefix.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(checks).to_csv(f"{prefix}_live_vs_cache.csv", index=False)
    pd.DataFrame(table).to_csv(f"{prefix}_test_table.csv", index=False, float_format="%.6g")
    print(f"wrote {prefix}_live_vs_cache.csv and {prefix}_test_table.csv")


if __name__ == "__main__":
    main()
