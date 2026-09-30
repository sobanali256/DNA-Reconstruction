"""Unit tests for the quality metrics (gate G2.5): hand-checked values plus a cross-check."""

import math
import random

import pytest

from dnarecon.metrics import (
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
