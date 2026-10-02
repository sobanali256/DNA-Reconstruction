"""Unit tests for the quality metrics (gate G2.5): hand-checked values plus a cross-check."""

import math
import random

import pandas as pd
import pytest

from dnarecon.metrics import (
    cascade_outcome,
    failure_auroc,
    edit_distance,
    exact_match,
    hamming_distance,
    normalized_edit_distance,
    score_cluster,
    summarize,
)

# (truth, prediction, edit distance, Hamming), each worked out by hand.
HAND_CHECKED = [
    ("ACGT", "ACGT", 0, 0),  # identical
    ("ACGT", "ACCT", 1, 1),  # one substitution
    ("ACGT", "AGT", 1, 3),  # a deletion shifts every later position
    ("ACGT", "AACGT", 1, 3),  # an insertion shifts too; the extra base is ignored
    ("ACGT", "ACGTA", 1, 0),  # a too-long tail costs nothing in Hamming
    ("ACGT", "", 4, 4),  # no bases at all
    ("AAAA", "TTTT", 4, 4),  # all wrong
    ("ACGTACGT", "TACGTACG", 2, 8),  # rotation: small edit distance, maximal Hamming
]


@pytest.mark.parametrize("truth,pred,ed,ham", HAND_CHECKED)
def test_hand_checked_values(truth, pred, ed, ham):
    assert edit_distance(pred, truth) == ed
    assert hamming_distance(pred, truth) == ham
    assert normalized_edit_distance(pred, truth) == ed / len(truth)
    assert exact_match(pred, truth) == (pred == truth)


def reference_edit_distance(a: str, b: str) -> int:
    """Plain dynamic-programming Levenshtein distance, independent of edlib."""
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def test_edlib_matches_reference_on_random_pairs():
    rng = random.Random(20260930)
    for _ in range(300):
        a = "".join(rng.choices("ACGT", k=rng.randint(0, 30)))
        b = "".join(rng.choices("ACGT", k=rng.randint(1, 30)))
        assert edit_distance(a, b) == reference_edit_distance(a, b), (a, b)


def test_hamming_matches_the_paper_formula_on_random_pairs():
    rng = random.Random(7)
    for _ in range(300):
        truth = "".join(rng.choices("ACGT", k=rng.randint(1, 20)))
        pred = "".join(rng.choices("ACGT", k=rng.randint(0, 25)))
        expected = sum(
            1 for i in range(1, len(truth) + 1)
            if len(pred) < i or truth[i - 1] != pred[i - 1]
        )
        assert hamming_distance(pred, truth) == expected


def test_no_output_scores_as_empty_prediction():
    score = score_cluster(None, "ACGT")
    assert not score.has_output and not score.exact_match
    assert (score.edit_distance, score.hamming_distance) == (4, 4)
    assert score.normalized_edit_distance == 1.0


def test_empty_string_counts_as_output():
    score = score_cluster("", "ACGT")
    assert score.has_output
    assert (score.edit_distance, score.hamming_distance) == (4, 4)


@pytest.mark.parametrize("truth", [None, ""])
def test_missing_ground_truth_is_an_error(truth):
    with pytest.raises(ValueError):
        score_cluster("ACGT", truth)
    with pytest.raises(ValueError):
        normalized_edit_distance("ACGT", truth)


def test_summarize_uses_all_clusters_as_denominator():
    scores = [
        score_cluster("ACGT", "ACGT"),  # exact
        score_cluster("ACCT", "ACGT"),  # ED 1, H 1
        score_cluster("AGT", "ACGT"),  # ED 1, H 3
        score_cluster(None, "ACGT"),  # failed: ED 4, H 4
    ]
    s = summarize(scores)
    assert (s.n_clusters, s.n_exact, s.n_failed, s.n_with_output) == (4, 1, 1, 3)
    assert s.exact_rate == 0.25 and s.failure_rate == 0.25
    assert s.mean_edit_distance == 6 / 4
    assert s.mean_hamming_distance == 8 / 4
    assert s.mean_normalized_edit_distance == 1.5 / 4
    assert s.mean_edit_distance_with_output == 2 / 3
    assert s.mean_hamming_distance_with_output == 4 / 3
    assert s.mean_normalized_edit_distance_with_output == 0.5 / 3


def test_summarize_all_failed_gives_nan_with_output_means():
    s = summarize([score_cluster(None, "ACGT")])
    assert s.exact_rate == 0 and s.failure_rate == 1
    assert math.isnan(s.mean_edit_distance_with_output)


def test_summarize_rejects_empty_input():
    with pytest.raises(ValueError):
        summarize([])


# Cached BBS + ITR outcomes, truth "ACGT" (expected length 4), worked out by hand:
#   a  confident, both right          b  BBS wrong, ITR right (rescue)
#   c  BBS right, ITR wrong (harm)    d  BBS wrong, ITR timed out (BBS kept)
#   e  empty cluster (no confidence)  f  BBS wrong, ITR closer but too short
CACHE = pd.DataFrame({
    "bbs_confidence": [0.9, 0.3, 0.4, 0.2, None, 0.5],
    "bbs_exact_match": [True, False, True, False, False, False],
    "bbs_edit_distance": [0, 2, 0, 3, 4, 2],
    "itr_failed": [False, False, False, True, None, False],
    "itr_sequence": ["ACGT", "ACGT", "ACGA", None, None, "ACG"],
    "itr_exact_match": [True, True, False, False, False, False],
    "itr_edit_distance": [0, 0, 1, 4, 4, 1],
    "itr_runtime_ms": [100.0, 200.0, 300.0, 400.0, None, 500.0],
    "expected_length": [4] * 6,
})


def test_cascade_tau_zero_is_bbs_only():
    out = cascade_outcome(CACHE, 0)
    assert (out["n_routed"], out["n_exact"], out["rescued"], out["harmed"]) == (0, 2, 0, 0)
    assert out["delta_exact_vs_bbs"] == 0 and math.isnan(out["routed_precision"])


def test_cascade_middle_tau():
    out = cascade_outcome(CACHE, 0.45)  # routes b, c, d
    assert (out["n_routed"], out["n_exact"], out["rescued"], out["harmed"]) == (3, 2, 1, 1)
    assert out["benefit_rate"] == 1 / 3 and out["harm_rate"] == 1 / 3  # d keeps BBS: unchanged
    assert out["error_capture_rate"] == 2 / 4 and out["routed_precision"] == 2 / 3
    assert out["itr_seconds"] == 0.9 and out["fallback_ratio"] == 0.5


def test_cascade_tau_one_routes_all_but_empty():
    out = cascade_outcome(CACHE, 1.0)
    assert (out["n_routed"], out["n_exact"], out["rescued"], out["harmed"]) == (5, 2, 1, 1)
    assert out["benefit_rate"] == 2 / 5 and out["harm_rate"] == 1 / 5
    checked = cascade_outcome(CACHE, 1.0, length_check=True)  # f's short ITR output is rejected
    assert checked["benefit_rate"] == 1 / 5 and checked["selector"] == "length_check"


def test_failure_auroc_hand_checked():
    # Positive scores {4, 2}, negative {3, 1}: 3 of 4 pairs ordered correctly.
    is_wrong = [True, False, True, False] * 50
    auc, low, high = failure_auroc(is_wrong, [4, 3, 2, 1] * 50, n_resamples=200, seed=1)
    assert auc == 0.75 and low <= auc <= high
    assert failure_auroc(is_wrong, [-4, -3, -2, -1] * 50, n_resamples=200, seed=1)[0] == 0.25


def test_cascade_routes_failed_bbs_shard_like_the_router():
    """bbs_status shard_failed counts as confidence 0; an empty cluster never routes."""
    df = CACHE.assign(bbs_status=["ok", "ok", "ok", "ok", "empty_cluster", "ok"])
    df.loc[0, ["bbs_status", "bbs_confidence", "bbs_exact_match"]] = ["shard_failed", None, False]
    out = cascade_outcome(df, 0.1)
    assert (out["n_routed"], out["rescued"]) == (1, 1)  # only the failed shard; ITR right
    assert cascade_outcome(df, 0)["n_routed"] == 0
    assert cascade_outcome(df, 1.0)["n_routed"] == 5  # e (empty) still never


def test_length_check_cache_matches_live_rule_after_bbs_failure():
    """Same rule as build_record (finding of the 2 Oct review): no BBS output -> ITR kept."""
    df = CACHE.assign(bbs_status="ok")
    df.loc[5, ["bbs_status", "bbs_confidence", "bbs_edit_distance"]] = ["shard_failed", None, 4]  # f: ITR 'ACG'
    assert cascade_outcome(df, 0.1)["n_routed"] == 1
    out = cascade_outcome(df, 0.1, length_check=True)
    assert out["n_routed"] == 1 and out["benefit_rate"] == 1.0  # ITR (ED 1) beats no output
