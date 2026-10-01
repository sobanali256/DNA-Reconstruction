"""Day 3 pilot summary: quality vs the BBS paper, ITR time per cluster, BBS variability.

    .venv/bin/python analysis/pilot_summary.py results/pilot_bbs-<stamp>-r* --itr results/pilot_itr-<stamp>-r1

The BBS folders must be all repetitions of one invocation (one <stamp>), in any order; repetition 1 is
the canonical run, the others are repeats used only for the variability numbers. Reads run folders only (never reruns anything) and writes
results/summary/pilot_summary.csv (long format: section, metric, value, paper, note).
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path

from dnarecon.metrics import ClusterScore, summarize
from dnarecon.models import ResultRecord
from dnarecon.results import read_run

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "results/summary/pilot_summary.csv"

# BBS paper (iScience 2025), Table 2, "Srinivasavaradhan et al." = Microsoft CNR, all
# 10,000 clusters. Its averages are compared with our with-output means.
PAPER = {
    "bbs": {"exact_rate": 0.9477, "mean_edit_distance": 0.168, "mean_hamming_distance": 1.616},
    "itr": {"exact_rate": 0.8758, "mean_edit_distance": 0.232, "mean_hamming_distance": 4.797},
}
FULL_DATASET = 10_000


def quality_rows(section: str, records: list[ResultRecord]) -> list[dict]:
    s = summarize([
        ClusterScore(r.final_sequence is not None, r.exact_match, r.edit_distance,
                     r.normalized_edit_distance, r.hamming_distance)
        for r in records
    ])
    paper = PAPER[section]
    return [
        row(section, "n_clusters", s.n_clusters),
        row(section, "n_empty_clusters", sum(r.failure_reason == "empty_cluster" for r in records)),
        row(section, "n_failed", s.n_failed, note="no output (empty clusters included)"),
        row(section, "exact_rate", s.exact_rate, paper["exact_rate"], "all clusters in the denominator"),
        row(section, "failure_rate", s.failure_rate),
        row(section, "mean_edit_distance_all", s.mean_edit_distance, note="failures count as length"),
        row(section, "mean_edit_distance_with_output", s.mean_edit_distance_with_output,
            paper["mean_edit_distance"]),
        row(section, "mean_normalized_edit_distance_all", s.mean_normalized_edit_distance),
        row(section, "mean_hamming_distance_all", s.mean_hamming_distance),
        row(section, "mean_hamming_distance_with_output", s.mean_hamming_distance_with_output,
            paper["mean_hamming_distance"]),
    ]


def itr_time_rows(records: list[ResultRecord], timing: list) -> list[dict]:
    ran = [r for r in records if r.itr_runtime_ms is not None]
    ok = sorted(r.itr_runtime_ms / 1000 for r in ran if r.itr_failed is False)
    stage = next(t for t in timing if t.kind == "stage")
    stage_s = stage.wall_time_ms / 1000
    per_cluster_wall = stage_s / stage.n_clusters
    counts = {s: sum(r.itr_status == s for r in records) for s in ("timeout", "crashed", "error", "task_failed")}
    rows = [
        row("itr_time", "clusters_run", len(ran)),
        row("itr_time", "mean_s_per_cluster", statistics.fmean(ok) if ok else None, 0.735,
            "successful clusters; paper value = 7,351.52 s / 10,000 on an i9-13900H"),
        row("itr_time", "median_s_per_cluster", statistics.median(ok) if ok else None),
        row("itr_time", "p95_s_per_cluster", ok[min(len(ok) - 1, int(0.95 * len(ok)))] if ok else None),
        row("itr_time", "max_s_per_cluster", ok[-1] if ok else None),
        row("itr_time", "sum_of_cluster_times_s", sum(r.itr_runtime_ms for r in ran) / 1000),
        row("itr_time", "stage_wall_s", stage_s, note="1 worker, includes process start-up"),
        row("itr_time", "stage_wall_s_per_cluster", per_cluster_wall),
        row("itr_time", "estimate_full_dataset_serial_h", per_cluster_wall * FULL_DATASET / 3600,
            note="stage wall time per cluster x 10,000"),
        row("itr_time", "estimate_full_dataset_4_workers_ideal_h",
            per_cluster_wall * FULL_DATASET / 3600 / 4, note="ideal speed-up; real will be lower"),
    ]
    rows += [row("itr_time", f"n_{s}", n) for s, n in counts.items()]
    return rows


def bbs_rows(runs: list[tuple[dict, list[ResultRecord], list]]) -> list[dict]:
    walls = [next(t for t in timing if t.kind == "stage").wall_time_ms / 1000 for _, _, timing in runs]
    exact = [sum(r.exact_match for r in recs) for _, recs, _ in runs]
    canonical = {r.cluster_id: r for r in runs[0][1]}
    changed, scores_changed, shard_failures = set(), set(), 0
    for _, recs, _ in runs[1:]:
        for r in recs:
            c = canonical[r.cluster_id]
            if "shard_failed" in (r.bbs_status, c.bbs_status):
                shard_failures += 1
                continue
            if r.bbs_status != "ok":  # empty cluster: nothing to compare
                continue
            if r.bbs_sequence != c.bbs_sequence:
                changed.add(r.cluster_id)
            if (r.bbs_k, r.bbs_path_weight, r.bbs_confidence) != (c.bbs_k, c.bbs_path_weight, c.bbs_confidence):
                scores_changed.add(r.cluster_id)
    max_conf = max((canonical[cid].bbs_confidence for cid in changed), default=None)
    threads = runs[0][0]["config"]["bbs"]["threads"]
    return [
        row("bbs_time", "repetitions", len(runs)),
        row("bbs_time", "stage_wall_s_median", statistics.median(walls), 20.01 * len(runs[0][1]) / FULL_DATASET,
            f"{threads} threads; paper value scaled from 20.01 s / 10,000 clusters on an i9-13900H"),
        row("bbs_time", "stage_wall_s_min", min(walls)),
        row("bbs_time", "stage_wall_s_max", max(walls)),
        row("bbs_variability", "clusters_with_changed_sequence", len(changed),
            note="vs the canonical run (repetition 1), over all repeats"),
        row("bbs_variability", "clusters_with_changed_k_weight_or_confidence", len(scores_changed),
            note="expected 0"),
        row("bbs_variability", "max_confidence_of_changed_clusters", max_conf),
        row("bbs_variability", "cluster_comparisons_skipped_shard_failed", shard_failures,
            note="expected 0"),
        row("bbs_variability", "exact_count_min", min(exact)),
        row("bbs_variability", "exact_count_max", max(exact)),
    ]


def row(section, metric, value, paper=None, note="") -> dict:
    return {"section": section, "metric": metric, "value": value, "paper": paper, "note": note}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bbs_runs", nargs="+", type=Path, help="BBS run folders; the first is canonical")
    parser.add_argument("--itr", type=Path, required=True, help="ITR run folder")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    # Sort by repetition number: the shell sorts r10 before r2.
    bbs_runs = sorted((read_run(d) for d in args.bbs_runs), key=lambda run: run[0]["repetition"])
    itr_run = read_run(args.itr)
    for manifest, _, _ in bbs_runs:
        if manifest["method"] != "bbs_only":
            sys.exit(f"{manifest['run_id']} is not a bbs_only run")
    # All BBS folders must be the complete set of repetitions of one invocation.
    groups = {m["run_id"].rsplit("-r", 1)[0] for m, _, _ in bbs_runs}
    reps = [m["repetition"] for m, _, _ in bbs_runs]
    if len(groups) != 1 or reps != list(range(1, bbs_runs[0][0]["repetitions"] + 1)):
        sys.exit(f"BBS folders must be repetitions 1..n of one invocation, got {sorted(groups)} "
                 f"repetitions {reps}")
    if itr_run[0]["method"] != "itr_only":
        sys.exit(f"{itr_run[0]['run_id']} is not an itr_only run")

    # Gate G3.5: every run covers exactly the same clusters.
    id_sets = [sorted(r.cluster_id for r in recs) for _, recs, _ in [*bbs_runs, itr_run]]
    if any(ids != id_sets[0] for ids in id_sets):
        sys.exit("runs cover different cluster IDs (gate G3.5)")

    rows = [*quality_rows("bbs", bbs_runs[0][1]), *quality_rows("itr", itr_run[1]),
            *itr_time_rows(itr_run[1], itr_run[2]), *bbs_rows(bbs_runs)]
    rows.append(row("runs", "bbs_canonical_run", bbs_runs[0][0]["run_id"]))
    rows.append(row("runs", "itr_run", itr_run[0]["run_id"]))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["section", "metric", "value", "paper", "note"],
                                lineterminator="\n")
        writer.writeheader()
        for r in rows:
            writer.writerow({k: _fmt(v) for k, v in r.items()})
    for r in rows:
        paper = f"   (paper {_fmt(r['paper'])})" if r["paper"] is not None else ""
        print(f"{r['section']:16s} {r['metric']:44s} {_fmt(r['value']):>14s}{paper}")
    print(f"Wrote {args.output}")


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


if __name__ == "__main__":
    main()
