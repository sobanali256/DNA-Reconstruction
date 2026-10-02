"""Unit tests for analysis/final_results.py: cache simulation, live-vs-cache check, table rows."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "analysis"))
from final_results import baselines, live_vs_cache, method_row, simulate  # noqa: E402

# 5 clusters: confidence ascending; c4 is empty (no reads). ITR output of c2 has the wrong length.
CACHE = pd.DataFrame({
    "cluster_id": ["c0", "c1", "c2", "c3", "c4"],
    "expected_length": 4,
    "bbs_status": ["ok", "ok", "ok", "ok", "empty_cluster"],
    "bbs_confidence": [0.2, 0.4, 0.6, 1.0, None],
    "bbs_sequence": ["AAAA", "CCCC", "GGGG", "TTTT", None],
    "bbs_exact_match": [False, True, True, True, False],
    "bbs_edit_distance": [2, 0, 0, 0, 4],
    "itr_failed": [False, False, False, False, None],
    "itr_sequence": ["ACGT", "CCCA", "GGG", "TTTT", None],
    "itr_exact_match": [True, False, False, True, None],
    "itr_edit_distance": [0, 1, 1, 0, 4],
    "itr_runtime_ms": 100.0,
})


def test_simulate_routes_below_tau_and_never_the_empty_cluster():
    out = simulate(CACHE, 0.8, "default")
    assert out.routed.tolist() == [True, True, True, False, False]
    assert out.ok.tolist() == [True, False, False, True, False]  # c1, c2 harmed
    assert out.has_output.tolist() == [True, True, True, True, False]
    assert out.ned.tolist() == [0, 0.25, 0.25, 0, 1]


def test_simulate_length_check_keeps_bbs_on_wrong_itr_length():
    out = simulate(CACHE, 0.8, "length_check")
    assert out.use_itr.tolist() == [True, True, False, False, False]
    assert out.final_sequence.tolist()[:3] == ["ACGT", "CCCA", "GGGG"]


def test_method_row_counts_rescued_harmed_and_mcnemar():
    base = baselines(CACHE)
    row = method_row(simulate(CACHE, 0.8, "default"), base["bbs_only"], "adaptive", "cache")
    assert (row["n_clusters"], row["n_exact"]) == (5, 2)  # empty cluster stays in the denominator
    assert (row["rescued_vs_bbs"], row["harmed_vs_bbs"]) == (1, 2)
    assert row["mcnemar_p"] == pytest.approx(1.0)
    assert row["mean_ned"] == pytest.approx(1.5 / 5)
    assert row["mean_ned_with_output"] == pytest.approx(0.5 / 4)
    bbs = method_row(base["bbs_only"], base["bbs_only"], "bbs_only", "cache")
    assert bbs["mcnemar_p"] is None and bbs["rescued_vs_bbs"] is None and bbs["exact_rate"] == 0.6


def test_live_vs_cache_flags_only_what_differs():
    pred = simulate(CACHE, 0.8, "default")
    live = pd.DataFrame({
        "cluster_id": CACHE.cluster_id,
        "bbs_confidence": CACHE.bbs_confidence,
        "bbs_sequence": ["AAAA", "CCCC", "GGGG", "TTTA", None],  # c3: BBS tie, different sequence
        "itr_sequence": ["ACGT", "CCCA", "GGG", None, None],
        "routed_to_itr": pred.routed,
        "final_sequence": ["ACGT", "CCCA", "GGG", "TTTA", None],
        "exact_match": [True, False, False, False, False],
    })
    checks = {r["check"]: r["value"] for r in live_vs_cache("adaptive", {"run_id": "r", "tau": 0.8,
                                                                         "selector": "default"}, live, CACHE)}
    assert checks["routing_differs"] == 0 and checks["bbs_confidence_differs"] == 0
    assert checks["bbs_sequence_differs"] == 1 and checks["bbs_sequence_differs_max_confidence"] == 1.0
    assert checks["itr_output_differs_on_routed"] == 0
    assert checks["final_sequence_differs"] == 1 and checks["exact_outcome_differs"] == 1
    assert (checks["n_exact_live"], checks["n_exact_cache"]) == (1, 2)
