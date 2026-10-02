"""Unit tests for result records: final selection, metrics, and the two CSV files."""

import pytest

from dnarecon.bbs_adapter import BbsClusterResult
from dnarecon.itr_adapter import ItrClusterResult
from dnarecon.models import ClusterRecord
from dnarecon.results import (
    TimingRecord,
    attach_metrics,
    build_record,
    read_per_cluster,
    read_timing,
    write_per_cluster,
    write_timing,
)

TRUTH = "ACGTACGT"


def cluster(cid="c1", reads=("ACGTACGT", "ACGTACG"), profile=None):
    return ClusterRecord(cid, tuple(reads), expected_length=8, dataset_id="t",
                         original_sequence=TRUTH, error_profile=profile)


def bbs(seq="ACGTACGA", cid="c1", confidence=0.4):
    return BbsClusterResult(cid, seq, k=5, path_weight=12.5, confidence=confidence)


def itr(status="ok", seq="ACGTACGT", cid="c1"):
    failed = status not in ("ok", "single_read")
    return ItrClusterResult(cid, status, "" if failed else seq, 900.0, 2,
                            None if failed else 2, "no row within 60 s" if failed else "")


def build(c=None, **kw):
    kw.setdefault("run_id", "r1")
    kw.setdefault("split", "dev")
    return build_record(c or cluster(), **kw)


# --- final selection: one test per row of the selection table ---

def test_empty_cluster_fails_for_every_method():
    empty = cluster(reads=())
    for method in ("bbs_only", "itr_only", "adaptive"):
        r = build(empty, method=method)
        assert (r.final_sequence, r.final_algorithm, r.status) == (None, "none", "failed")
        assert r.failure_reason == "empty_cluster" and not r.routed_to_itr
    r = build(empty, method="adaptive")
    assert (r.bbs_status, r.itr_status) == ("empty_cluster", "empty_cluster")
    assert build(empty, method="bbs_only").itr_status == "not_run"


def test_not_routed_keeps_bbs():
    r = build(method="adaptive", bbs=bbs(), bbs_shard_id="s0")
    assert (r.final_sequence, r.final_algorithm, r.status) == ("ACGTACGA", "bbs", "ok")
    assert (r.routed_to_itr, r.itr_status, r.itr_failed, r.worker_id) == (False, "not_routed", None, None)
    assert (r.bbs_k, r.bbs_path_weight, r.bbs_confidence, r.bbs_shard_id) == (5, 12.5, 0.4, "s0")
    assert r.total_cluster_runtime_ms is None


@pytest.mark.parametrize("status", ["ok", "single_read"])
def test_routed_itr_success_wins(status):
    r = build(method="adaptive", bbs=bbs(), itr=itr(status), itr_task_id="t3", worker_id=2)
    assert (r.final_sequence, r.final_algorithm, r.status) == (TRUTH, "itr", "ok")
    assert (r.routed_to_itr, r.itr_failed, r.worker_id, r.itr_task_id) == (True, False, 2, "t3")
    assert r.itr_runtime_ms == 900.0 and r.total_cluster_runtime_ms is None


@pytest.mark.parametrize("status", ["timeout", "crashed", "error"])
def test_routed_itr_failure_falls_back_to_bbs(status):
    r = build(method="adaptive", bbs=bbs(), itr=itr(status), worker_id=1)
    assert (r.final_sequence, r.final_algorithm, r.status, r.failure_reason) == ("ACGTACGA", "bbs", "ok", "")
    assert (r.itr_status, r.itr_failed, r.itr_sequence) == (status, True, None)


def test_itr_only_failure_fails_the_cluster():
    r = build(method="itr_only", itr=itr("timeout"))
    assert (r.final_sequence, r.final_algorithm, r.status) == (None, "none", "failed")
    assert r.failure_reason == "itr_timeout: no row within 60 s"
    assert r.bbs_status == "not_run"


def test_itr_only_success_has_total_runtime():
    r = build(method="itr_only", itr=itr())
    assert (r.final_algorithm, r.total_cluster_runtime_ms, r.routed_to_itr) == ("itr", 900.0, True)


def test_bbs_shard_failure_fails_the_cluster():
    r = build(method="adaptive", bbs_shard_failed=True, bbs_shard_id="s1")
    assert (r.bbs_status, r.final_algorithm, r.status, r.failure_reason) == (
        "shard_failed", "none", "failed", "bbs_shard_failed")
    assert r.itr_status == "not_routed"


def test_bbs_shard_failure_routed_to_itr_is_rescued():
    """No BBS output counts as confidence 0, so the cluster may go to ITR (2 Oct 2026)."""
    r = build(method="adaptive", bbs_shard_failed=True, itr=itr())
    assert (r.bbs_status, r.routed_to_itr, r.final_algorithm, r.status) == ("shard_failed", True, "itr", "ok")


def test_bbs_shard_failure_and_itr_failure_report_both():
    r = build(method="adaptive", bbs_shard_failed=True, itr=itr(status="timeout"))
    assert (r.final_algorithm, r.status) == ("none", "failed")
    assert r.failure_reason.startswith("bbs_shard_failed; itr_timeout")
    r = build(method="adaptive", bbs_shard_failed=True, itr_task_failed=True)
    assert r.failure_reason == "bbs_shard_failed; itr_task_failed"


def test_failed_itr_task_is_routed_and_falls_back_to_bbs():
    r = attach_metrics(build(method="adaptive", bbs=bbs(), itr_task_failed=True,
                             itr_task_id="t1", worker_id=1), TRUTH)
    assert (r.routed_to_itr, r.itr_status, r.itr_failed, r.itr_task_id) == (True, "task_failed", True, "t1")
    assert (r.final_algorithm, r.status, r.failure_reason) == ("bbs", "ok", "")
    assert (r.itr_exact_match, r.itr_edit_distance) == (False, 8)


def test_failed_itr_task_fails_an_itr_only_cluster():
    r = build(method="itr_only", itr_task_failed=True)
    assert (r.final_algorithm, r.status, r.failure_reason) == ("none", "failed", "itr_task_failed")


def test_empty_bbs_string_is_an_output():
    r = build(method="bbs_only", bbs=bbs(seq=""))
    assert (r.final_sequence, r.final_algorithm, r.status) == ("", "bbs", "ok")


@pytest.mark.parametrize("kw", [
    dict(method="nope"),
    dict(method="bbs_only"),  # missing BBS result
    dict(method="itr_only"),  # missing ITR result
    dict(method="bbs_only", bbs=bbs(), itr=itr()),  # ITR in a BBS-only run
    dict(method="itr_only", bbs=bbs(), itr=itr()),  # BBS in an ITR-only run
    dict(method="adaptive", bbs=bbs(cid="other")),  # wrong cluster
    dict(method="adaptive", bbs=bbs(), worker_id=1),  # worker without ITR
    dict(method="adaptive", bbs=bbs(), itr=itr(), itr_task_failed=True),  # result + failed task
    dict(method="bbs_only", bbs=bbs(), itr_task_failed=True),  # ITR task in a BBS-only run
    dict(method="itr_only", itr=itr(), bbs_shard_failed=True),  # BBS shard in an ITR-only run
])
def test_inconsistent_inputs_are_rejected(kw):
    with pytest.raises(ValueError):
        build(**kw)


def test_empty_cluster_with_engine_result_is_rejected():
    with pytest.raises(ValueError):
        build(cluster(reads=()), method="bbs_only", bbs=bbs())


# --- metrics ---

def test_metrics_for_final_bbs_and_itr():
    r = attach_metrics(build(method="adaptive", bbs=bbs(), itr=itr(), worker_id=0), TRUTH)
    assert (r.exact_match, r.edit_distance, r.hamming_distance) == (True, 0, 0)
    assert r.normalized_edit_distance == 0.0
    assert (r.bbs_exact_match, r.bbs_edit_distance, r.bbs_hamming_distance) == (False, 1, 1)
    assert (r.itr_exact_match, r.itr_edit_distance, r.itr_hamming_distance) == (True, 0, 0)


def test_metrics_leave_unrun_engines_blank():
    r = attach_metrics(build(method="adaptive", bbs=bbs()), TRUTH)
    assert r.itr_exact_match is None and r.itr_edit_distance is None
    r = attach_metrics(build(method="itr_only", itr=itr()), TRUTH)
    assert r.bbs_exact_match is None


def test_metrics_score_failures_as_empty_prediction():
    r = attach_metrics(build(method="adaptive", bbs=bbs(), itr=itr("timeout")), TRUTH)
    assert (r.itr_exact_match, r.itr_edit_distance, r.itr_hamming_distance) == (False, 8, 8)
    assert r.exact_match is False and r.edit_distance == 1  # final = BBS
    r = attach_metrics(build(cluster(reads=()), method="adaptive"), TRUTH)
    assert (r.edit_distance, r.bbs_edit_distance, r.itr_edit_distance) == (8, 8, 8)


def test_metrics_attach_only_once():
    r = attach_metrics(build(method="bbs_only", bbs=bbs()), TRUTH)
    with pytest.raises(ValueError):
        attach_metrics(r, TRUTH)


# --- files ---

def sample_records():
    rows = [
        build(cluster("a"), method="adaptive", bbs=bbs(cid="a"), bbs_shard_id="s0"),
        build(cluster("b", profile={"ins": 0.01, "del": 0.02}), method="adaptive",
              bbs=bbs(seq="", cid="b"), itr=itr("timeout", cid="b"), itr_task_id="t0", worker_id=3),
        build(cluster("c"), method="adaptive", bbs=bbs(cid="c"), itr=itr(cid="c"), worker_id=0),
        build(cluster("d", reads=()), method="adaptive"),
    ]
    return [attach_metrics(r, TRUTH) for r in rows]


def test_per_cluster_round_trip(tmp_path):
    records = sample_records()
    path = tmp_path / "run" / "per_cluster.csv"
    write_per_cluster(records, path, expected_cluster_ids="abcd")
    assert read_per_cluster(path) == records


def test_ground_truth_never_written(tmp_path):
    records = [attach_metrics(build(cluster("a"), method="adaptive", bbs=bbs(cid="a")), TRUTH)]
    path = tmp_path / "per_cluster.csv"
    write_per_cluster(records, path, expected_cluster_ids=["a"])
    assert TRUTH not in path.read_text()


def test_writer_refuses_overwrite(tmp_path):
    path = tmp_path / "per_cluster.csv"
    write_per_cluster(sample_records(), path, expected_cluster_ids="abcd")
    with pytest.raises(FileExistsError):
        write_per_cluster(sample_records(), path, expected_cluster_ids="abcd")


@pytest.mark.parametrize("ids", ["abc", "abcde"])
def test_writer_rejects_missing_or_extra_clusters(tmp_path, ids):
    with pytest.raises(ValueError):
        write_per_cluster(sample_records(), tmp_path / "p.csv", expected_cluster_ids=ids)
    assert not (tmp_path / "p.csv").exists()


def test_writer_rejects_duplicates_and_mixed_runs(tmp_path):
    records = sample_records()
    with pytest.raises(ValueError):
        write_per_cluster(records + records[:1], tmp_path / "p.csv", expected_cluster_ids="abcd")
    records[0] = build(cluster("a"), run_id="other", method="adaptive", bbs=bbs(cid="a"))
    with pytest.raises(ValueError):
        write_per_cluster(records, tmp_path / "p.csv", expected_cluster_ids="abcd")


def test_timing_round_trip(tmp_path):
    rows = [
        TimingRecord("r1", "bbs_shard", "s0", None, 10, 4, 1.5, 2.5, 1000.25, 1, "ok"),
        TimingRecord("r1", "itr_task", "t0", 2, 4, 1, 2.5, 6.0, 3500.0, 2, "cluster_failures"),
        TimingRecord("r1", "run", "r1", None, 10, None, None, None, 4600.0, None, "ok"),
    ]
    path = tmp_path / "timing.csv"
    write_timing(rows, path)
    assert read_timing(path) == rows
    with pytest.raises(FileExistsError):
        write_timing(rows, path)
    with pytest.raises(ValueError):
        write_timing([TimingRecord("r1", "bogus", "x", None, 0, None, None, None, 0.0, None, "ok")],
                     tmp_path / "t2.csv")


def test_length_check_keeps_itr_when_bbs_has_no_output():
    """After a failed BBS shard, ITR's answer is the only one, even with the wrong length."""
    short = itr(seq=TRUTH[:-1])
    r = build(method="adaptive", bbs_shard_failed=True, itr=short, selector="length_check")
    assert (r.final_algorithm, r.status) == ("itr", "ok")
    r = build(method="adaptive", bbs=bbs(), itr=short, selector="length_check")
    assert r.final_algorithm == "bbs"
