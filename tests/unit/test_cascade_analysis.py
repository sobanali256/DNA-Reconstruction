"""Unit tests for the per-condition grouping in analysis/cascade_from_cache.py."""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "analysis"))
from cascade_from_cache import cascade_rows, groups, with_group  # noqa: E402

from dnarecon.metrics import cascade_outcome  # noqa: E402

# Two conditions, 4 clusters each: BBS right/wrong x ITR right/wrong, confidence ascending.
CACHE = pd.DataFrame({
    "dataset_id": ["a"] * 4 + ["b"] * 4,
    "expected_length": 4,
    "bbs_status": "ok",
    "bbs_confidence": [0.2, 0.4, 0.6, 1.0] * 2,
    "bbs_path_weight": [1.0, 2.0, 3.0, 4.0] * 2,
    "bbs_exact_match": [False, False, True, True, False, True, True, True],
    "bbs_edit_distance": [2, 1, 0, 0, 1, 0, 0, 0],
    "itr_failed": False,
    "itr_sequence": "ACGT",
    "itr_exact_match": [True, False, False, True, True, True, False, True],
    "itr_edit_distance": [0, 1, 1, 0, 0, 0, 1, 0],
    "itr_runtime_ms": 100.0,
})


def test_groups_without_group_by_is_the_whole_table():
    [(name, g)] = groups(CACHE, None)
    assert name is None and g is CACHE
    rows = [{"metric": "x"}]
    assert with_group(None, None, rows) == rows  # ungrouped tables keep their shape


def test_groups_pooled_first_then_each_condition():
    out = groups(CACHE, "dataset_id")
    assert [name for name, _ in out] == ["all", "a", "b"]
    assert [len(g) for _, g in out] == [8, 4, 4]
    assert with_group("a", "dataset_id", [{"metric": "x"}]) == [{"dataset_id": "a", "metric": "x"}]


def test_pooled_counts_are_the_sum_of_the_groups():
    for tau in (0, 0.5, 1.0):
        pooled, *parts = [cascade_outcome(g, tau) for _, g in groups(CACHE, "dataset_id")]
        for key in ("n_clusters", "n_routed", "n_exact", "rescued", "harmed"):
            assert pooled[key] == sum(p[key] for p in parts)


def test_two_by_two_per_group_and_auroc_skipped_with_few_failures():
    rows = {r["metric"]: r for r in cascade_rows(CACHE[CACHE.dataset_id == "a"], 50, 1)}
    assert [rows[f"bbs_{b}_fallback_{i}"]["value"] for b in ("right", "wrong") for i in ("right", "wrong")] == [1, 1, 1, 1]
    assert rows["confidence"]["value"] is None and "fewer than" in rows["confidence"]["note"]
