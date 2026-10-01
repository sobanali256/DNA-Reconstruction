"""Unit tests for the experiment runner, with stub engines that fail on purpose."""

import hashlib
import json
import stat
import sys
from pathlib import Path

import pytest
import yaml

from dnarecon import runner
from dnarecon.models import ClusterRecord
from dnarecon.results import read_per_cluster, read_timing
from dnarecon.dataset import save_records_jsonl
from dnarecon.runner import DirtyTreeError, _chunks, load_clusters, load_config, run_experiment

TRUTH = "ACGT"


def clusters():
    return [
        ClusterRecord("a", ("ACGT", "ACGA"), 4, "t", original_sequence=TRUTH),
        ClusterRecord("b", ("ACGT",), 4, "t", original_sequence=TRUTH),
        ClusterRecord("e", (), 4, "t", original_sequence=TRUTH),
    ]


SPLITS = {"a": "dev", "b": "dev", "e": "dev"}


def stub(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\nimport sys\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.prov, "provenance", lambda: {"project_dirty": False})
    (tmp_path / "list.csv").write_text("cluster_id,n_reads\na,2\nb,1\ne,0\n")
    save_records_jsonl(clusters(), tmp_path / "r.jsonl")
    (tmp_path / "split.csv").write_text("cluster_id,split\na,dev\nb,test\ne,dev\n")
    (tmp_path / "ds.yaml").write_text(yaml.safe_dump({"records_path": "r.jsonl",
                                                      "split": {"output_path": "split.csv"}}))
    bbs = stub(tmp_path, "bbs", "sys.exit(1)")  # every BBS shard fails
    itr = stub(tmp_path, "itr", "sys.exit(2)")  # itr_cli's 'malformed input' exit code
    (tmp_path / "bbs.yaml").write_text(yaml.safe_dump(
        {"binary": str(bbs), "beam_width": 20, "k_min": 4, "k_max": 62, "alpha": 1}))
    (tmp_path / "itr.yaml").write_text(yaml.safe_dump(
        {"binary": str(itr), "seed": 1, "timeout_s_per_cluster": 5}))
    return tmp_path


def config(method="bbs_only", **extra):
    cfg = {"experiment_name": "t", "dataset": "ds.yaml", "clusters": "list.csv",
           "method": method, "repetitions": 1, "output_root": "out"}
    if method == "bbs_only":
        cfg["bbs"] = {"config": "bbs.yaml", "threads": 1, "shards": 2}
    else:
        cfg["itr"] = {"config": "itr.yaml", "workers": 1, "microbatch_size": 1}
    cfg.update(extra)
    return cfg


def write_config(tmp_path, cfg):
    path = tmp_path / "exp.yaml"
    path.write_text(yaml.safe_dump(cfg))
    return path


@pytest.mark.parametrize("method", ["bbs_only", "itr_only"])
def test_valid_configs_load(tmp_path, method):
    assert load_config(write_config(tmp_path, config(method)))["method"] == method


@pytest.mark.parametrize("change", [
    {"method": "adaptive"},
    {"method": "nope"},
    {"repetitions": 0},
    {"experiment_name": "bad name/"},
    {"bbs": {"config": "bbs.yaml", "threads": 1}},  # shards missing
    {"bbs": {"config": "bbs.yaml", "threads": 0, "shards": 1}},
    {"split": "train"},
])
def test_invalid_bbs_configs_rejected(tmp_path, change):
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, {**config("bbs_only"), **change}))


@pytest.mark.parametrize("itr", [
    {"config": "itr.yaml", "workers": 2, "microbatch_size": 1},  # needs the scheduler
    {"config": "itr.yaml", "workers": 1, "microbatch_size": 0},
    {"config": "itr.yaml", "workers": 1},
])
def test_invalid_itr_configs_rejected(tmp_path, itr):
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, config("itr_only", itr=itr)))


def test_missing_key_rejected(tmp_path):
    cfg = config()
    del cfg["clusters"]
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, cfg))


def test_dirty_tree_refused(root, monkeypatch):
    monkeypatch.setattr(runner.prov, "provenance", lambda: {"project_dirty": True})
    with pytest.raises(DirtyTreeError):
        run_experiment(config(), clusters(), SPLITS, root=root, log=lambda m: None)
    assert not (root / "out").exists()
    run_experiment(config(), clusters(), SPLITS, root=root, allow_dirty=True, log=lambda m: None)


def test_failed_bbs_shards_are_recorded_and_run_completes(root):
    (run_dir,) = run_experiment(config(), clusters(), SPLITS, root=root, log=lambda m: None)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["status"] == "complete" and manifest["n_empty_clusters"] == 1
    rows = {r.cluster_id: r for r in read_per_cluster(run_dir / "per_cluster.csv")}
    assert rows["a"].failure_reason == rows["b"].failure_reason == "bbs_shard_failed"
    assert (rows["a"].bbs_shard_id, rows["b"].bbs_shard_id) == ("s0", "s1")  # 2 shards
    assert rows["e"].failure_reason == "empty_cluster"
    timing = read_timing(run_dir / "timing.csv")
    assert [t.kind for t in timing] == ["bbs_shard", "bbs_shard", "stage", "run"]
    assert all(t.status == "failed" for t in timing)  # the run row follows the stage


def test_failed_itr_tasks_are_recorded_and_run_completes(root):
    (run_dir,) = run_experiment(config("itr_only"), clusters(), SPLITS, root=root, log=lambda m: None)
    rows = {r.cluster_id: r for r in read_per_cluster(run_dir / "per_cluster.csv")}
    assert rows["a"].itr_status == rows["b"].itr_status == "task_failed"
    assert (rows["a"].itr_task_id, rows["b"].itr_task_id, rows["a"].worker_id) == ("t0", "t1", 0)
    assert rows["e"].itr_status == "empty_cluster" and rows["e"].worker_id is None
    assert rows["a"].itr_edit_distance == 4  # scored as an ITR failure
    kinds = [t.kind for t in read_timing(run_dir / "timing.csv")]
    assert kinds == ["itr_task", "itr_task", "stage", "run"]


def test_repetitions_get_their_own_folders(root):
    dirs = run_experiment(config(repetitions=3), clusters(), SPLITS, root=root, log=lambda m: None)
    assert [d.name[-3:] for d in dirs] == ["-r1", "-r2", "-r3"]
    assert all((d / "per_cluster.csv").exists() for d in dirs)


def test_existing_run_folder_is_never_reused(root, monkeypatch):
    monkeypatch.setattr(runner.time, "strftime", lambda fmt: "20260101-000000")
    run_experiment(config(), clusters(), SPLITS, root=root, log=lambda m: None)
    with pytest.raises(FileExistsError):
        run_experiment(config(), clusters(), SPLITS, root=root, log=lambda m: None)


def test_crash_marks_the_run_failed_and_keeps_the_folder(root, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(runner, "run_shard", boom)
    with pytest.raises(RuntimeError):
        run_experiment(config(), clusters(), SPLITS, root=root, log=lambda m: None)
    (run_dir,) = (root / "out").iterdir()
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["status"] == "failed" and "unexpected" in manifest["error"]
    assert manifest["ended_at"] is not None


def test_clusters_without_split_rejected(root):
    with pytest.raises(ValueError):
        run_experiment(config(), clusters(), {"a": "dev"}, root=root, log=lambda m: None)


def test_split_key_keeps_only_that_split(root):
    pick = lambda **k: [c.cluster_id for c in load_clusters(config(**k), root)[0]]
    assert (pick(), pick(split="dev"), pick(split="test")) == (["a", "b", "e"], ["a", "e"], ["b"])


def test_listed_cluster_without_split_is_an_error_not_dropped(root):
    (root / "split.csv").write_text("cluster_id,split\na,dev\ne,dev\n")  # b lost its row
    with pytest.raises(ValueError, match="not in the dataset or its split"):
        load_clusters(config(split="dev"), root)


def test_manifest_records_dataset_file_hash(root):
    (run_dir,) = run_experiment(config(), clusters(), SPLITS, root=root, log=lambda m: None)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["dataset_files_sha256"] == {"r.jsonl": hashlib.sha256((root / "r.jsonl").read_bytes()).hexdigest()}


def test_log_never_shows_test_accuracy(root):
    lines = []
    run_experiment(config(), clusters(), {"a": "dev", "b": "test", "e": "test"}, root=root,
                   log=lines.append)
    assert "0/1 dev exact" in lines[-1]


def test_chunks_are_contiguous_and_balanced():
    assert _chunks(list(range(7)), 3) == [[0, 1, 2], [3, 4], [5, 6]]
    assert _chunks([1, 2], 5) == [[1], [2]]
    assert _chunks([], 2) == []
