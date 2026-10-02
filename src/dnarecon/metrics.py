"""Reconstruction-quality metrics (design doc v3, section 7.1).

This is the only module that sees ground truth, and only after reconstruction is done.

Conventions (decided 30 Sep 2026):
- Edit distance is the Levenshtein distance (global alignment, edlib `NW` mode).
- Hamming distance follows the BBS paper (iScience 2025, STAR Methods) exactly:
  d_H = sum over i = 1..L of I(len(pred) < i or truth_i != pred_i), L = len(truth).
  Bases past the end of the truth are not counted, so a too-long tail costs nothing.
  It is a secondary metric, kept for comparison with the paper's Table 2.
- A cluster with no output (empty cluster, crash, timeout) is scored as an empty
  prediction: edit distance = Hamming = len(truth), normalized edit distance = 1.
  `summarize` also reports means over the clusters with output only, which is what
  we compare with the paper (it does not say how it handled missing outputs).
- Every rate uses all eligible clusters as the denominator.

`cascade_outcome` and `failure_auroc` (Day 4) work on cached BBS-only + ITR-only results,
one DataFrame row per cluster (see analysis/cascade_from_cache.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import edlib
import numpy as np
import pandas as pd
from scipy.stats import bootstrap
from sklearn.metrics import roc_auc_score


def exact_match(pred: str, truth: str) -> bool:
    return pred == truth


def edit_distance(pred: str, truth: str) -> int:
    return edlib.align(pred, truth, mode="NW", task="distance")["editDistance"]


def normalized_edit_distance(pred: str, truth: str) -> float:
    length = _check_truth(truth)
    return edit_distance(pred, truth) / length


def hamming_distance(pred: str, truth: str) -> int:
    """Length-aware Hamming distance as defined in the BBS paper (see module docstring)."""
    return sum(1 for i, base in enumerate(truth) if i >= len(pred) or pred[i] != base)


@dataclass(frozen=True)
class ClusterScore:
    has_output: bool  # False: no output at all (scored as an empty prediction)
    exact_match: bool
    edit_distance: int
    normalized_edit_distance: float
    hamming_distance: int


def score_cluster(pred: str | None, truth: str) -> ClusterScore:
    """Score one cluster. `pred` is None when the cluster produced no output.

    An empty string is an output (BBS returns "" when it finds no candidate); it scores
    the same as no output but still counts in the with-output means.
    """
    length = _check_truth(truth)
    seq = "" if pred is None else pred
    distance = edit_distance(seq, truth)
    return ClusterScore(
        has_output=pred is not None,
        exact_match=exact_match(seq, truth),
        edit_distance=distance,
        normalized_edit_distance=distance / length,
        hamming_distance=hamming_distance(seq, truth),
    )


@dataclass(frozen=True)
class MetricSummary:
    n_clusters: int  # all eligible clusters: the denominator of every rate
    n_exact: int
    exact_rate: float
    n_failed: int  # clusters with no output
    failure_rate: float
    mean_edit_distance: float
    mean_normalized_edit_distance: float
    mean_hamming_distance: float
    n_with_output: int
    mean_edit_distance_with_output: float  # nan when no cluster has output
    mean_normalized_edit_distance_with_output: float
    mean_hamming_distance_with_output: float


def summarize(scores: Sequence[ClusterScore]) -> MetricSummary:
    if not scores:
        raise ValueError("no clusters to summarize")
    with_output = [s for s in scores if s.has_output]
    n = len(scores)
    n_exact = sum(s.exact_match for s in scores)
    n_failed = n - len(with_output)
    return MetricSummary(
        n_clusters=n,
        n_exact=n_exact,
        exact_rate=n_exact / n,
        n_failed=n_failed,
        failure_rate=n_failed / n,
        mean_edit_distance=_mean(s.edit_distance for s in scores),
        mean_normalized_edit_distance=_mean(s.normalized_edit_distance for s in scores),
        mean_hamming_distance=_mean(s.hamming_distance for s in scores),
        n_with_output=len(with_output),
        mean_edit_distance_with_output=_mean(s.edit_distance for s in with_output),
        mean_normalized_edit_distance_with_output=_mean(
            s.normalized_edit_distance for s in with_output
        ),
        mean_hamming_distance_with_output=_mean(s.hamming_distance for s in with_output),
    )


def cascade_outcome(df: pd.DataFrame, tau: float, length_check: bool = False) -> dict:
    """The adaptive pipeline at threshold `tau`, simulated from cached BBS and ITR results.

    Needs the columns bbs_confidence, bbs_exact_match, bbs_edit_distance, itr_failed,
    itr_sequence, itr_exact_match, itr_edit_distance, itr_runtime_ms, expected_length.
    Routing as dnarecon.router: BBS confidence < tau; a cluster whose BBS shard failed
    (bbs_status shard_failed) counts as confidence 0; an empty cluster never routes.
    Selection copies dnarecon.results.build_record: a successful ITR result wins, else BBS.
    `length_check` (label-free variant) also keeps BBS when ITR's output length differs
    from the designed length. Benefit/harm compare the final edit distance with BBS's
    among routed clusters, so an ITR failure (BBS kept) counts as unchanged.
    """
    routed = df.bbs_confidence < tau
    if "bbs_status" in df:  # no BBS output (failed shard) = confidence 0
        routed |= df.bbs_status.eq("shard_failed") & (tau > 0)
    use_itr = routed & df.itr_failed.eq(False)
    if length_check:
        use_itr &= df.itr_sequence.str.len().eq(df.expected_length)
    bbs_ok = df.bbs_exact_match.astype(bool)
    final_ok = df.itr_exact_match.where(use_itr, bbs_ok).astype(bool)
    final_ed = df.itr_edit_distance.where(use_itr, df.bbs_edit_distance)
    n_routed = int(routed.sum())
    n_bbs_wrong = int((~bbs_ok).sum())
    caught = int((routed & ~bbs_ok).sum())
    return {
        "tau": tau,
        "selector": "length_check" if length_check else "default",
        "n_clusters": len(df),
        "n_routed": n_routed,
        "fallback_ratio": n_routed / len(df),
        "n_exact": int(final_ok.sum()),
        "exact_rate": final_ok.mean(),
        "delta_exact_vs_bbs": final_ok.mean() - bbs_ok.mean(),
        "rescued": int((final_ok & ~bbs_ok).sum()),
        "harmed": int((~final_ok & bbs_ok).sum()),
        "benefit_rate": _ratio((routed & (final_ed < df.bbs_edit_distance)).sum(), n_routed),
        "harm_rate": _ratio((routed & (final_ed > df.bbs_edit_distance)).sum(), n_routed),
        "error_capture_rate": _ratio(caught, n_bbs_wrong),
        "routed_precision": _ratio(caught, n_routed),
        "itr_seconds": df.itr_runtime_ms[routed].sum() / 1000,
    }


def failure_auroc(is_wrong, score, n_resamples: int, seed: int) -> tuple[float, float, float]:
    """AUROC of `score` (higher = more likely wrong) for BBS failure, with a 95% bootstrap CI.

    A resample with only one class has no AUROC (nan); with few failures the CI can be nan.
    """
    data = (np.asarray(is_wrong, dtype=bool), np.asarray(score, dtype=float))

    def auroc(y, s):
        return roc_auc_score(y, s) if 0 < y.sum() < len(y) else np.nan

    ci = bootstrap(data, auroc, paired=True, vectorized=False, n_resamples=n_resamples,
                   rng=np.random.default_rng(seed)).confidence_interval
    return roc_auc_score(*data), ci.low, ci.high


def _ratio(num, den) -> float:
    return num / den if den else math.nan


def _check_truth(truth: str | None) -> int:
    if not truth:
        raise ValueError("ground truth is missing or empty")
    return len(truth)


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else math.nan
