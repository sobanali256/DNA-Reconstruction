"""Unit tests for the ITR adapter, using a fake wrapper that can hang, crash or misbehave.

The fake reads the same input format as itr_cli and answers with the first read as the
"reconstruction". Its behaviour per cluster is chosen by the cluster ID prefix:
hang-* sleeps, crash-* aborts, err-* reports an exception row, bad-* prints garbage.
"""

import stat
import sys
from pathlib import Path

import pytest

from dnarecon.itr_adapter import ItrRunError, ItrSettings, parse_row, run_itr_batch, write_input
from dnarecon.models import ClusterRecord

FAKE = '''#!{python}
import os, sys, time
seed, path = sys.argv[2], sys.argv[3]
print("cluster_id\\tstatus\\treads_in\\treads_used\\truntime_ms\\tsequence", flush=True)
lines = open(path).read().splitlines()
i = 0
errors = 0
while i < len(lines):
    cid, length, n = lines[i][1:].split()
    reads = lines[i + 1:i + 1 + int(n)]
    i += 1 + int(n)
    if cid.startswith("hang"):
        time.sleep(30)
    if cid.startswith("crash"):
        os.abort()
    if cid.startswith("bad"):
        print("garbage", flush=True)
        continue
    if cid.startswith("err"):
        errors += 1
        print(f"{{cid}}\\terror\\t{{n}}\\t{{min(int(n), 25)}}\\t1.000\\t", flush=True)
        continue
    status = "single_read" if int(n) == 1 else "ok"
    print(f"{{cid}}\\t{{status}}\\t{{n}}\\t{{min(int(n), 25)}}\\t1.000\\t{{reads[0]}}", flush=True)
sys.exit(1 if errors else 0)
'''


@pytest.fixture
def fake(tmp_path) -> ItrSettings:
    path = tmp_path / "fake_itr_cli"
    path.write_text(FAKE.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return ItrSettings(binary=path, seed=7, timeout_s_per_cluster=1.0)


def rec(cid: str, reads=("ACGT", "ACGA"), truth="GGGG") -> ClusterRecord:
    return ClusterRecord(cid, tuple(reads), expected_length=4, dataset_id="t", original_sequence=truth)


def statuses(batch):
    return [(r.cluster_id, r.status) for r in batch.results]


def test_all_ok_returns_in_order_and_cleans_up(fake, tmp_path):
    work = tmp_path / "work"
    batch = run_itr_batch([rec("a"), rec("b", reads=("TTTT",)), rec("c")], fake, workdir=work, task_id="t1")
    assert statuses(batch) == [("a", "ok"), ("b", "single_read"), ("c", "ok")]
    assert batch.results[0].sequence == "ACGT" and batch.launches == 1
    assert batch.command[1:3] == ["--seed", "7"]
    assert list(work.iterdir()) == []  # scratch files removed after a clean task


def test_timeout_fails_one_cluster_and_resumes(fake, tmp_path):
    work = tmp_path / "work"
    batch = run_itr_batch([rec("a"), rec("hang-b"), rec("c")], fake, workdir=work, task_id="t2",
                          timeout_s_per_cluster=0.5)
    assert statuses(batch) == [("a", "ok"), ("hang-b", "timeout"), ("c", "ok")]
    assert batch.launches == 2
    assert batch.results[1].itr_failed and batch.results[1].sequence == ""
    assert batch.results[1].runtime_ms >= 500
    assert any(p.name.startswith("t2.itr.input") for p in work.iterdir())  # kept on failure


def test_crash_fails_one_cluster_and_resumes(fake, tmp_path):
    batch = run_itr_batch([rec("crash-a"), rec("b"), rec("crash-c")], fake,
                          workdir=tmp_path, task_id="t3")
    assert statuses(batch) == [("crash-a", "crashed"), ("b", "ok"), ("crash-c", "crashed")]
    assert batch.launches == 2  # b and crash-c run in the second process
    assert "exited with" in batch.results[0].failure_reason


def test_wrapper_error_row_is_a_cluster_failure(fake, tmp_path):
    batch = run_itr_batch([rec("a"), rec("err-b")], fake, workdir=tmp_path, task_id="t4")
    assert statuses(batch) == [("a", "ok"), ("err-b", "error")]
    assert batch.results[1].itr_failed


def test_garbage_output_is_a_task_error(fake, tmp_path):
    with pytest.raises(ItrRunError, match="malformed wrapper row"):
        run_itr_batch([rec("bad-a")], fake, workdir=tmp_path, task_id="t5")


def test_missing_binary_is_a_task_error(tmp_path):
    settings = ItrSettings(binary=tmp_path / "nope", seed=1, timeout_s_per_cluster=1)
    with pytest.raises(ItrRunError, match="cannot start"):
        run_itr_batch([rec("a")], settings, workdir=tmp_path, task_id="t6")


def test_rejects_empty_and_duplicate_clusters(fake, tmp_path):
    with pytest.raises(ValueError, match="empty clusters"):
        run_itr_batch([rec("a", reads=())], fake, workdir=tmp_path, task_id="t7")
    with pytest.raises(ValueError, match="duplicate"):
        run_itr_batch([rec("a"), rec("a")], fake, workdir=tmp_path, task_id="t7")
    with pytest.raises(ValueError, match="empty micro-batch"):
        run_itr_batch([], fake, workdir=tmp_path, task_id="t7")


def test_input_has_no_ground_truth(tmp_path):
    path = tmp_path / "in.txt"
    write_input([rec("a", truth="GGGGCCCC")], path)
    assert path.read_text() == ">a 4 2\nACGT\nACGA\n"


def test_slow_start_counts_as_first_cluster_timeout(fake, tmp_path):
    # A deadline shorter than process start-up: every cluster times out, none raises.
    batch = run_itr_batch([rec("a"), rec("b")], fake, workdir=tmp_path, task_id="t8",
                          timeout_s_per_cluster=1e-6)
    assert statuses(batch) == [("a", "timeout"), ("b", "timeout")]
    assert batch.launches == 2



@pytest.mark.parametrize("line", [
    "a\tok\tx\t2\t1.0\tACGT",        # non-numeric count
    "b\tok\t2\t2\t1.0\tACGT",        # wrong cluster
    "a\tok\t3\t3\t1.0\tACGT",        # wrong read count
    "a\tok\t2\t2\t1.0\t",            # success without a sequence
    "a\terror\t2\t2\t1.0\tACGT",     # failure with a sequence
    "a\tok\t2\t2\t1.0\tACGN",        # non-ACGT output
])
def test_parse_row_rejects_bad_rows(line):
    with pytest.raises(ItrRunError):
        parse_row(line, rec("a"))
