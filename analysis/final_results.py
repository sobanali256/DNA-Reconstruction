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
                              p-values are not corrected for multiple comparisons) and its
                              log10 (exact in log space, so tiny p-values do not print as 0).
Methods: bbs_only and itr_only (cache runs), and one per live run. Test adaptive rows come
from the live runs, paired with the live run's own BBS results (so BBS tie-breaking between
runs never counts as a cascade effect); dev adaptive rows are cache simulations at the same
τ and selector, paired with the cache BBS run. Empty clusters stay in every denominator as
failures.

Stops when a live run and its cache runs used different inputs (split, dataset, clusters
file, engine commits or settings), when routing, BBS confidence or a routed ITR output
differs, or when a run's truth length differs from expected_length (edit distances are
normalized by it). An exact-match outcome that differs (BBS tie-breaking) only warns: the
live run stays the headline.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy.special import logsumexp
from scipy.stats import binom, binomtest

from cascade_from_cache import ROOT, groups, load_cache
from dnarecon.metrics import cascade_frame
from dnarecon.results import read_run

BASELINES = ("bbs_only", "itr_only")
FATAL_CHECKS = ("routing_differs", "bbs_confidence_differs", "itr_output_differs_on_routed")


def simulate(df: pd.DataFrame, tau: float, selector: str) -> pd.DataFrame:
    """Per-cluster cache prediction of the adaptive pipeline (metrics.cascade_frame)."""
    frame = cascade_frame(df, tau, length_check=selector == "length_check")
    return pd.DataFrame({
        "cluster_id": df.cluster_id,
        "routed": frame.routed.astype(bool),
        "use_itr": frame.use_itr.astype(bool),
        "final_sequence": df.itr_sequence.where(frame.use_itr, df.bbs_sequence),
        "ok": frame.ok,
        "ned": frame.ed / df.expected_length,
        "has_output": frame.use_itr | frame.has_bbs,
    })


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


def check_normalization(name: str, per_cluster: pd.DataFrame) -> None:
    """Stop unless truth length = expected_length for every cluster (NED = ED / expected_length)."""
    if not np.allclose(per_cluster.normalized_edit_distance,
                       per_cluster.edit_distance / per_cluster.expected_length):
        sys.exit(f"{name}: truth length differs from expected_length; cannot normalize edit distances")


def check_same_inputs(live_manifest: dict, cache_manifests: list[dict]) -> None:
    """Stop unless the live run and its cache runs used the same split, data and engines."""
    def inputs(m: dict, engine: str | None) -> dict:
        prov = m["provenance"]
        out = {"split": m["config"].get("split"), "dataset": m["dataset_files_sha256"],
               "clusters_file": m["clusters_file_sha256"],
               "bbs_commit": prov["bbs_commit"], "itr_commit": prov["itr_commit"]}
        if engine:  # adaptive: {"bbs": ..., "itr": ...}; single-engine runs: the engine itself
            out["engine"] = m["engine_config"][engine] if "bbs" in m["engine_config"] else m["engine_config"]
        return out

    engine_of = {"bbs_only": "bbs", "itr_only": "itr"}
    for cache in cache_manifests:
        engine = engine_of[cache["method"]]
        live, other = inputs(live_manifest, engine), inputs(cache, engine)
        bad = [k for k in live if live[k] != other[k]]
        if bad:
            sys.exit(f"{live_manifest['run_id']} vs {cache['run_id']}: different {', '.join(bad)}")


def load_live(run_dir: Path) -> tuple[dict, pd.DataFrame]:
    manifest, records, _ = read_run(run_dir)
    live = pd.DataFrame(map(asdict, records))
    if manifest["method"] != "adaptive":
        sys.exit(f"{run_dir.name}: not an adaptive run")
    check_normalization(run_dir.name, live)
    return manifest, live


def live_frame(live: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({"cluster_id": live.cluster_id, "routed": live.routed_to_itr.astype(bool),
                         "ok": live.exact_match.astype(bool), "ned": live.normalized_edit_distance,
                         "has_output": live.status.eq("ok")})


def live_bbs_frame(live: pd.DataFrame) -> pd.DataFrame:
    """The live run's own BBS results: the paired baseline for its rescued / harmed counts."""
    return pd.DataFrame({"cluster_id": live.cluster_id, "ok": live.bbs_exact_match.astype(bool)})


def live_vs_cache(name: str, manifest: dict, live: pd.DataFrame, cache: pd.DataFrame) -> list[dict]:
    """Rows comparing one live run with the cache prediction at the same τ and selector.

    Stops on a routing, BBS confidence or routed-ITR-output difference (wrong inputs or
    engines); warns on an exact-match outcome difference (BBS tie-breaking).
    """
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
    fatal = {k: checks[k] for k in FATAL_CHECKS if checks[k]}
    if fatal:
        sys.exit(f"{manifest['run_id']}: live run differs from the cache: {fatal}")
    if checks["exact_outcome_differs"]:
        print(f"WARNING {manifest['run_id']}: {checks['exact_outcome_differs']} exact-match outcomes "
              f"differ from the cache (BBS tie-breaking); the live run stays the headline", file=sys.stderr)
    return [{"method": name, "run_id": manifest["run_id"], "check": k, "value": v} for k, v in checks.items()]


def mcnemar(rescued: int, harmed: int) -> tuple[float | None, float | None]:
    """Exact two-sided McNemar p-value and its log10 (computed in log space: never -inf)."""
    n = rescued + harmed
    if not n:
        return None, None
    # log P(X <= k) as a log-sum of log-pmf terms: binom.logcdf underflows to -inf here
    log_cdf = logsumexp(binom.logpmf(np.arange(min(rescued, harmed) + 1), n, 0.5))
    log_p = min(0.0, math.log(2) + log_cdf)
    return binomtest(rescued, n).pvalue, log_p / math.log(10)


def method_row(m: pd.DataFrame, paired_bbs: pd.DataFrame, method: str, source: str, baseline: str) -> dict:
    """One table row; rescued / harmed / McNemar against `paired_bbs` (cluster_id, ok)."""
    n, k = len(m), int(m.ok.sum())
    ci = binomtest(k, n).proportion_ci(method="wilson")
    paired = m.merge(paired_bbs[["cluster_id", "ok"]], on="cluster_id", suffixes=("", "_bbs"), validate="one_to_one")
    rescued = int((paired.ok & ~paired.ok_bbs).sum())
    harmed = int((~paired.ok & paired.ok_bbs).sum())
    is_bbs = method == "bbs_only"
    p, log10_p = (None, None) if is_bbs else mcnemar(rescued, harmed)
    return {
        "method": method, "source": source, "n_clusters": n, "n_exact": k,
        "exact_rate": k / n, "exact_ci95_low": ci.low, "exact_ci95_high": ci.high,
        "mean_ned": m.ned.mean(), "n_with_output": int(m.has_output.sum()),
        "mean_ned_with_output": m.ned[m.has_output].mean(),
        "routed_share": m.routed.mean(),
        "paired_baseline": None if is_bbs else baseline,
        "rescued_vs_bbs": None if is_bbs else rescued,
        "harmed_vs_bbs": None if is_bbs else harmed,
        "mcnemar_p": p,
        "mcnemar_log10_p": log10_p,
    }


def family_tables(name: str, fam: dict) -> tuple[list[dict], list[dict]]:
    group_by = fam.get("group_by")
    clash = set(fam["live"]) & set(BASELINES)
    if clash:
        sys.exit(f"{name}: live method names {sorted(clash)} clash with the baselines")
    caches, cache_manifests = {}, {}
    for split in ("dev", "test"):
        runs = [ROOT / fam[split]["bbs_run"], ROOT / fam[split]["itr_run"]]
        df, _, method = load_cache(*runs, split)
        if method != "itr_only":
            sys.exit(f"{name} {split}: need a BBS-only and an ITR-only run")
        for run in runs:
            check_normalization(run.name, pd.read_csv(
                run / "per_cluster.csv", usecols=["edit_distance", "normalized_edit_distance", "expected_length"]))
        caches[split] = df
        cache_manifests[split] = [json.loads((run / "manifest.json").read_text()) for run in runs]
    lives = {method: load_live(ROOT / run) for method, run in fam["live"].items()}
    for manifest, _ in lives.values():
        check_same_inputs(manifest, cache_manifests["test"])

    check_rows = [{"family": name, **r} for method, (man, live) in lives.items()
                  for r in live_vs_cache(method, man, live, caches["test"])]

    # per split and method: (frame, source, paired BBS baseline, baseline label)
    frames: dict[str, dict[str, tuple[pd.DataFrame, str, pd.DataFrame, str]]] = {}
    for split, df in caches.items():
        base = baselines(df)
        frames[split] = {m: (f, "cache", base["bbs_only"], "cache bbs_only") for m, f in base.items()}
        for method, (man, live) in lives.items():
            frames[split][method] = (
                (live_frame(live), "live", live_bbs_frame(live), "live run's own BBS") if split == "test" else
                (simulate(df, man["tau"], man["selector"]), "cache simulation", base["bbs_only"], "cache bbs_only"))
    table = []
    for split, df in caches.items():
        for group, g in groups(df, group_by):
            ids = set(g.cluster_id)
            for method, (f, src, bbs, label) in frames[split].items():
                manifest = lives[method][0] if method in lives else None
                table.append({"family": name, "group": group or "all", "split": split,
                              "tau": manifest["tau"] if manifest else None,
                              "selector": manifest["selector"] if manifest else None,
                              **method_row(f[f.cluster_id.isin(ids)], bbs[bbs.cluster_id.isin(ids)],
                                           method, src, label)})
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
