"""Why ITR breaks clusters BBS gets right, and why it rarely rescues BBS failures.

    .venv/bin/python analysis/itr_failure_modes.py configs/pilot_cascade.yaml

Same config and clusters as analysis/cascade_from_cache.py (the fallback run must be
ITR-only). Clusters with reads are grouped by exact match (both_right, harmed = BBS right
and ITR wrong, rescued, both_wrong), then:
  * per group: coverage, ITR output length, ITR error types and how many of them fall in
    homopolymer runs (>= 4 equal bases) of the true strand;
  * reads: at the run where ITR's only error is one deletion (harmed clusters), the share
    of reads that shorten, keep or lengthen that run, compared with the first such run in
    the both_right clusters.
Ground truth is used for evaluation only. Writes <prefix>_itr_failure_modes.csv (long
format: section, metric, value, note).
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

import edlib
import numpy as np
import pandas as pd
import yaml

from cascade_from_cache import ROOT, load_cache, row
from dnarecon.dataset import load_microsoft

LONG_RUN = 4  # homopolymer length counted as "long"
GROUPS = {(True, True): "both_right", (True, False): "harmed",
          (False, True): "rescued", (False, False): "both_wrong"}


def columns(query: str, truth: str):
    """Alignment columns (op, truth position, query base); op is one of = X D I.

    An insertion gets the position of the next truth base; a deletion has no query base.
    """
    cigar = edlib.align(query, truth, mode="NW", task="path")["cigar"] or ""
    t = q = 0
    for n, op in re.findall(r"(\d+)([=XDI])", cigar):
        for _ in range(int(n)):
            yield op, t, (query[q] if op != "D" else None)
            t += op != "I"
            q += op != "D"


def run_bounds(seq: str, pos: int) -> tuple[int, int]:
    """First and last index of the homopolymer run containing seq[pos]."""
    left, right = pos, pos
    while left > 0 and seq[left - 1] == seq[pos]:
        left -= 1
    while right < len(seq) - 1 and seq[right + 1] == seq[pos]:
        right += 1
    return left, right


def read_run_length(read: str, truth: str, left: int, right: int) -> int:
    """Bases of `read` aligned to truth[left..right] (inserted ones included) that equal the run base."""
    return sum(base == truth[left] for op, t, base in columns(read, truth)
               if op != "D" and left <= t <= right + (op == "I"))


def group_rows(df: pd.DataFrame) -> list[dict]:
    truth_runs = [right - left + 1 for t in df.truth for left, right in map(lambda p: run_bounds(t, p), range(len(t)))]
    rows = [row("background", "share_truth_positions_in_long_runs", float(np.mean(np.array(truth_runs) >= LONG_RUN)),
                f"runs >= {LONG_RUN} equal bases")]
    for name, g in df.groupby("group"):
        errors = [(op, run_bounds(r.truth, min(t, len(r.truth) - 1)))
                  for r in g.itertuples() for op, t, _ in columns(r.itr_sequence or "", r.truth) if op != "="]
        ops = Counter(op for op, _ in errors)
        in_long = [right - left + 1 >= LONG_RUN for _, (left, right) in errors]
        length_off = g.itr_sequence.fillna("").str.len() - g.expected_length
        rows += [
            row(name, "n_clusters", len(g)),
            row(name, "coverage_median", float(g.coverage.median())),
            row(name, "itr_edit_distance_median", float(g.itr_edit_distance.median())),
            row(name, "itr_length_wrong_share", float((length_off != 0).mean()), "label-free: length != designed"),
            row(name, "itr_one_base_short", int((length_off == -1).sum())),
            row(name, "itr_errors_substitution", ops["X"]),
            row(name, "itr_errors_deletion", ops["D"]),
            row(name, "itr_errors_insertion", ops["I"]),
            row(name, "itr_errors_in_long_runs_share", float(np.mean(in_long)) if in_long else None,
                f"truth homopolymer >= {LONG_RUN}"),
        ]
    return rows


def read_rows(df: pd.DataFrame) -> list[dict]:
    rows = []
    for name in ("harmed", "both_right"):
        shares = []
        for r in df[df.group == name].itertuples():
            if name == "harmed":  # the run holding ITR's single deleted base
                m = re.fullmatch(r"(\d+)=1D\d*=?", edlib.align(r.itr_sequence, r.truth, mode="NW", task="path")["cigar"])
                pos = int(m.group(1)) if m else None
            else:  # the first long run of the strand
                pos = next((p for p in range(len(r.truth)) if np.diff(run_bounds(r.truth, p))[0] + 1 >= LONG_RUN), None)
            if pos is None:
                continue
            left, right = run_bounds(r.truth, pos)
            if right - left + 1 < LONG_RUN:
                continue
            lengths = np.array([read_run_length(read, r.truth, left, right) for read in r.reads])
            run = right - left + 1
            shares.append(((lengths < run).mean(), (lengths == run).mean(), (lengths > run).mean()))
        s = np.array(shares).reshape(-1, 3)
        rows += [
            row(f"reads_{name}", "n_clusters", len(s), "harmed: ITR's only error is one deletion in a long run"
                if name == "harmed" else "first long run of the strand"),
            row(f"reads_{name}", "share_reads_run_shortened", float(s[:, 0].mean()) if len(s) else None),
            row(f"reads_{name}", "share_reads_run_correct", float(s[:, 1].mean()) if len(s) else None),
            row(f"reads_{name}", "share_reads_run_lengthened", float(s[:, 2].mean()) if len(s) else None),
            row(f"reads_{name}", "share_clusters_majority_shortened", float((s[:, 0] > 0.5).mean()) if len(s) else None),
        ]
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", type=Path)
    cfg = yaml.safe_load(parser.parse_args().config.read_text())
    df, run_ids, fallback_method = load_cache(ROOT / cfg["bbs_run"], ROOT / cfg["fallback_run"], cfg["split"])
    if fallback_method != "itr_only":
        sys.exit("the fallback run must be ITR-only")

    dataset = yaml.safe_load((ROOT / cfg["dataset"]).read_text())
    ids = set(df.cluster_id)
    clusters = {c.cluster_id: c for c in load_microsoft(ROOT / dataset["clusters_path"], ROOT / dataset["centers_path"],
                                                        dataset["expected_length"]) if c.cluster_id in ids}
    df = df[df.bbs_status == "ok"].assign(  # clusters with reads
        truth=lambda x: x.cluster_id.map(lambda c: clusters[c].original_sequence),
        reads=lambda x: x.cluster_id.map(lambda c: clusters[c].reads),
        coverage=lambda x: x.reads.map(len),
        group=lambda x: [GROUPS[bool(b), bool(i)] for b, i in zip(x.bbs_exact_match, x.itr_exact_match)],
    )

    out = pd.DataFrame(group_rows(df) + read_rows(df) + [row("runs", "bbs_run", run_ids[0]),
                                                          row("runs", "itr_run", run_ids[1])])
    path = (ROOT / cfg["output_prefix"]).with_name(Path(cfg["output_prefix"]).name + "_itr_failure_modes.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False, lineterminator="\n")
    print(out.to_string(index=False), f"\nWrote {path}")


if __name__ == "__main__":
    main()
