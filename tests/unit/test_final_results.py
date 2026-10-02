"""Unit tests for analysis/final_results.py: cache simulation, live-vs-cache check, table rows."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "analysis"))
from final_results import (  # noqa: E402
    baselines, check_same_inputs, family_tables, live_bbs_frame, live_vs_cache, mcnemar, method_row, simulate,
)

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
    row = method_row(simulate(CACHE, 0.8, "default"), base["bbs_only"], "adaptive", "cache", "cache bbs_only")
    assert (row["n_clusters"], row["n_exact"]) == (5, 2)  # empty cluster stays in the denominator
    assert (row["rescued_vs_bbs"], row["harmed_vs_bbs"]) == (1, 2)
    assert row["mcnemar_p"] == pytest.approx(1.0)
    assert row["mean_ned"] == pytest.approx(1.5 / 5)
    assert row["mean_ned_with_output"] == pytest.approx(0.5 / 4)
    bbs = method_row(base["bbs_only"], base["bbs_only"], "bbs_only", "cache", "cache bbs_only")
    assert bbs["mcnemar_p"] is None and bbs["rescued_vs_bbs"] is None and bbs["exact_rate"] == 0.6


def live_run(**changes):
    """A live adaptive run equal to the cache at τ 0.8, except c3's BBS tie (sequence TTTA, wrong)."""
    pred = simulate(CACHE, 0.8, "default")
    return pd.DataFrame({
        "cluster_id": CACHE.cluster_id,
        "bbs_confidence": CACHE.bbs_confidence,
        "bbs_sequence": ["AAAA", "CCCC", "GGGG", "TTTA", None],  # c3: BBS tie, different sequence
        "itr_sequence": ["ACGT", "CCCA", "GGG", None, None],
        "routed_to_itr": pred.routed,
        "final_sequence": ["ACGT", "CCCA", "GGG", "TTTA", None],
        "exact_match": [True, False, False, False, False],
        "bbs_exact_match": [False, True, True, False, False],
    }).assign(**changes)


MANIFEST = {"run_id": "r", "tau": 0.8, "selector": "default"}


def test_live_vs_cache_flags_only_what_differs(capsys):
    checks = {r["check"]: r["value"] for r in live_vs_cache("adaptive", MANIFEST, live_run(), CACHE)}
    assert checks["routing_differs"] == 0 and checks["bbs_confidence_differs"] == 0
    assert checks["bbs_sequence_differs"] == 1 and checks["bbs_sequence_differs_max_confidence"] == 1.0
    assert checks["itr_output_differs_on_routed"] == 0
    assert checks["final_sequence_differs"] == 1 and checks["exact_outcome_differs"] == 1
    assert (checks["n_exact_live"], checks["n_exact_cache"]) == (1, 2)
    assert "WARNING" in capsys.readouterr().err  # exact outcome differs: warn, keep going


@pytest.mark.parametrize("changes", [
    {"routed_to_itr": [True, True, False, False, False]},          # routing
    {"bbs_confidence": [0.2, 0.4, 0.6, 0.9, None]},                # BBS confidence
    {"itr_sequence": ["ACGA", "CCCA", "GGG", None, None]},         # routed ITR output
])
def test_live_vs_cache_stops_on_wrong_inputs(changes):
    with pytest.raises(SystemExit):
        live_vs_cache("adaptive", MANIFEST, live_run(**changes), CACHE)


def test_live_rows_pair_with_the_live_runs_own_bbs():
    live = live_run()
    own = method_row(pd.DataFrame({"cluster_id": live.cluster_id, "routed": False, "ok": live.exact_match,
                                   "ned": 0.0, "has_output": True}),
                     live_bbs_frame(live), "adaptive", "live", "live run's own BBS")
    cache = method_row(pd.DataFrame({"cluster_id": live.cluster_id, "routed": False, "ok": live.exact_match,
                                     "ned": 0.0, "has_output": True}),
                       baselines(CACHE)["bbs_only"], "adaptive", "live", "cache bbs_only")
    # c3's BBS tie flipped to wrong in the live run: against the cache BBS it looks like harm
    assert (own["rescued_vs_bbs"], own["harmed_vs_bbs"]) == (1, 2)
    assert (cache["rescued_vs_bbs"], cache["harmed_vs_bbs"]) == (1, 3)


def test_mcnemar_log10_p_never_underflows():
    p, log10_p = mcnemar(1381, 20)
    assert p == 0.0 and -400 < log10_p < -300  # the float p underflows, the log does not
    p, log10_p = mcnemar(5, 3)
    assert 10 ** log10_p == pytest.approx(p)
    assert mcnemar(4, 4)[1] == 0.0 and mcnemar(0, 0) == (None, None)


def manifest(method, **override):
    m = {"run_id": method, "method": method, "config": {"split": "test"},
         "dataset_files_sha256": {"d": "x"}, "clusters_file_sha256": "c",
         "provenance": {"bbs_commit": "b", "itr_commit": "i"},
         "engine_config": {"binary": method, "beam_width": 20}}
    return {**m, **override}


def test_check_same_inputs_accepts_matching_runs_and_stops_on_any_difference():
    live = manifest("adaptive", engine_config={"bbs": manifest("bbs_only")["engine_config"],
                                               "itr": manifest("itr_only")["engine_config"]})
    check_same_inputs(live, [manifest("bbs_only"), manifest("itr_only")])
    for bad in ({"dataset_files_sha256": {"d": "y"}}, {"config": {"split": "dev"}},
                {"provenance": {"bbs_commit": "b2", "itr_commit": "i"}},
                {"engine_config": {"binary": "bbs_only", "beam_width": 100}}):
        with pytest.raises(SystemExit):
            check_same_inputs(live, [manifest("bbs_only", **bad), manifest("itr_only")])


def test_live_method_named_like_a_baseline_is_rejected():
    with pytest.raises(SystemExit, match="clash"):
        family_tables("f", {"live": {"bbs_only": "results/x"}})
