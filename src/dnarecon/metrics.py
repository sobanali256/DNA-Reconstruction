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
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import edlib


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


def _check_truth(truth: str | None) -> int:
    if not truth:
        raise ValueError("ground truth is missing or empty")
    return len(truth)


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else math.nan
