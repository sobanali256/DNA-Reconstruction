"""Unit tests for the experiment runner, with stub engines that fail on purpose."""

import hashlib
import json
import stat
import sys
from pathlib import Path

import pytest
import yaml

from dnarecon import runner
from dnarecon.bbs_adapter import BbsClusterResult, BbsShardResult
from dnarecon.itr_adapter import ItrBatchResult, ItrClusterResult, ItrRunError
from dnarecon.models import ClusterRecord
from dnarecon.results import read_per_cluster, read_timing
from dnarecon.dataset import save_records_jsonl
from dnarecon.runner import DirtyTreeError, load_clusters, load_config, run_experiment
from dnarecon.scheduler import contiguous_blocks

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
    if method in ("bbs_only", "adaptive"):
        cfg["bbs"] = {"config": "bbs.yaml", "threads": 1, "shards": 2}
    if method in ("itr_only", "adaptive"):
        cfg["itr"] = {"config": "itr.yaml", "workers": 1, "microbatch_size": 1}
    if method == "adaptive":
        cfg["tau"] = 0.8
    cfg.update(extra)
    return cfg


def write_config(tmp_path, cfg):
    path = tmp_path / "exp.yaml"
    path.write_text(yaml.safe_dump(cfg))
    return path


@pytest.mark.parametrize("method", ["bbs_only", "itr_only", "adaptive"])
def test_valid_configs_load(tmp_path, method):
    assert load_config(write_config(tmp_path, config(method)))["method"] == method


@pytest.mark.parametrize("change", [
    {"method": "adaptive"},  # no tau, no itr section
    {"tau": 0.5},  # tau on a non-adaptive run
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
    {"config": "itr.yaml", "workers": 2, "microbatch_size": 1},  # serial scheduler needs 1 worker
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


def test_blocks_are_contiguous_and_balanced():
    assert contiguous_blocks(list(range(7)), 3) == [[0, 1, 2], [3, 4], [5, 6]]
    assert contiguous_blocks([1, 2], 5) == [[1], [2]]
    assert contiguous_blocks([], 2) == []


# --- adaptive runs (gates G3.4, G3.5), with fake engines -------------------------------

def adaptive_clusters():
    """c1..c4 with BBS confidence 0.1, 0.5, 0.9, 1.0 (fake engines below), plus an empty one."""
    return [*(ClusterRecord(f"c{i}", ("ACGT", "ACGT"), 4, "t", original_sequence=TRUTH) for i in range(1, 5)),
            ClusterRecord("e", (), 4, "t", original_sequence=TRUTH)]


CONFIDENCE = {"c1": 0.1, "c2": 0.5, "c3": 0.9, "c4": 1.0}


@pytest.fixture
def fake_engines(monkeypatch):
    """BBS answers 'ACGA' (wrong) with fixed confidences; ITR answers per a mutable plan."""
    plan = {"itr": {}, "itr_task_fail": set(), "seen": []}

    def fake_bbs(chunk, settings, *, length, threads, workdir, shard_id, timeout_s=None):
        res = [BbsClusterResult(c.cluster_id, "ACGA", 4, 1.0, CONFIDENCE[c.cluster_id]) for c in chunk]
        return BbsShardResult(shard_id, res, 0.01, threads, ["bbs"], 0.0, 0.01)

    def fake_itr(batch, settings, *, workdir, task_id, timeout_s_per_cluster=None, cancel=None):
        plan["seen"] += [c.cluster_id for c in batch]
        if any(c.cluster_id in plan["itr_task_fail"] for c in batch):
            raise ItrRunError("boom")
        res = [ItrClusterResult(c.cluster_id, *plan["itr"].get(c.cluster_id, ("ok", "ACGT")), 5.0, 2, 2)
               for c in batch]
        return ItrBatchResult(task_id, res, 0.0, 0.01, 0.01, 1, ["itr"])

    monkeypatch.setattr(runner, "run_shard", fake_bbs)
    monkeypatch.setattr(runner, "run_itr_batch", fake_itr)
    return plan


def run_adaptive(root, **extra):
    cs = adaptive_clusters()
    splits = {c.cluster_id: "dev" for c in cs}
    (run_dir,) = run_experiment(config("adaptive", **extra), cs, splits, root=root, log=lambda m: None)
    rows = {r.cluster_id: r for r in read_per_cluster(run_dir / "per_cluster.csv")}
    return run_dir, rows


def test_adaptive_routes_below_tau_and_itr_wins(root, fake_engines):
    run_dir, rows = run_adaptive(root)
    assert fake_engines["seen"] == ["c1", "c2"]  # ITR sees only the routed clusters
    assert list(rows) == ["c1", "c2", "c3", "c4", "e"]  # G3.5: every cluster, list order
    assert all(r.final_algorithm and r.routed_to_itr is not None for r in rows.values())  # G3.4
    assert [rows[c].routed_to_itr for c in rows] == [True, True, False, False, False]
    assert [rows[c].final_algorithm for c in rows] == ["itr", "itr", "bbs", "bbs", "none"]
    assert (rows["c3"].itr_status, rows["e"].itr_status) == ("not_routed", "empty_cluster")
    assert rows["c1"].exact_match and not rows["c3"].exact_match
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert (manifest["tau"], manifest["selector"], manifest["n_routed"]) == (0.8, "default", 2)
    assert manifest["routed_ids_sha256"] == hashlib.sha256(b"c1\nc2").hexdigest()
    assert set(manifest["engine_config"]) == {"bbs", "itr"}
    kinds = [(t.kind, t.id) for t in read_timing(run_dir / "timing.csv")]
    assert [k for k in kinds if k[0] == "stage"] == [("stage", "bbs"), ("stage", "route"), ("stage", "itr")]
    assert [k for k in kinds if k[0] == "itr_task"] == [("itr_task", "t0"), ("itr_task", "t1")]
    assert kinds[-1] == ("run", run_dir.name)


def test_adaptive_tau_zero_is_bbs_only(root, fake_engines):
    _, rows = run_adaptive(root, tau=0)
    assert fake_engines["seen"] == [] and not any(r.routed_to_itr for r in rows.values())


def test_adaptive_itr_failure_keeps_bbs(root, fake_engines):
    fake_engines["itr"]["c1"] = ("timeout", "")
    fake_engines["itr_task_fail"].add("c2")
    _, rows = run_adaptive(root)
    for cid, status in (("c1", "timeout"), ("c2", "task_failed")):
        r = rows[cid]
        assert (r.routed_to_itr, r.itr_failed, r.itr_status) == (True, True, status)
        assert (r.final_algorithm, r.final_sequence, r.status) == ("bbs", "ACGA", "ok")


def test_adaptive_length_check_keeps_bbs_on_wrong_length(root, fake_engines):
    fake_engines["itr"]["c1"] = ("ok", "ACG")  # one base short
    _, rows = run_adaptive(root, selector="length_check")
    assert (rows["c1"].final_algorithm, rows["c1"].itr_sequence) == ("bbs", "ACG")
    assert rows["c2"].final_algorithm == "itr"
    _, rows = run_adaptive(root, experiment_name="t2")  # default selector takes ITR's answer
    assert rows["c1"].final_algorithm == "itr"


def test_adaptive_failed_bbs_shard_goes_to_itr(root, fake_engines, monkeypatch):
    """No BBS output counts as confidence 0: routed whenever tau > 0; empty clusters never."""
    def failing_bbs(chunk, settings, **kwargs):
        from dnarecon.bbs_adapter import BbsRunError
        raise BbsRunError("down")
    monkeypatch.setattr(runner, "run_shard", failing_bbs)
    run_dir, rows = run_adaptive(root, tau=0.05)
    assert fake_engines["seen"] == ["c1", "c2", "c3", "c4"]
    assert all((r.bbs_status, r.final_algorithm, r.status) == ("shard_failed", "itr", "ok")
               for cid, r in rows.items() if cid != "e")
    assert rows["e"].failure_reason == "empty_cluster" and not rows["e"].routed_to_itr
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert (manifest["n_bbs_failed"], manifest["n_routed_without_bbs"]) == (4, 4)
    stages = {t.id: t.status for t in read_timing(run_dir / "timing.csv") if t.kind in ("stage", "run")}
    assert stages["bbs"] == "failed" and stages[run_dir.name] == "failed"  # still visible


def test_adaptive_tau_zero_never_routes_even_without_bbs_output(root, fake_engines, monkeypatch):
    def failing_bbs(chunk, settings, **kwargs):
        from dnarecon.bbs_adapter import BbsRunError
        raise BbsRunError("down")
    monkeypatch.setattr(runner, "run_shard", failing_bbs)
    _, rows = run_adaptive(root, tau=0)
    assert fake_engines["seen"] == []
    assert all(r.failure_reason in ("bbs_shard_failed", "empty_cluster") for r in rows.values())


# --- scheduler and warm-up in the runner (G5.2, G5.3, G5.6) -----------------------------

@pytest.mark.parametrize("mode,workers", [("serial", 1), ("dynamic", 3), ("static", 3), ("static_lpt", 2)])
def test_itr_scheduler_modes_cover_every_cluster_once(root, fake_engines, mode, workers):
    cs = adaptive_clusters()
    splits = {c.cluster_id: "dev" for c in cs}
    cfg = config("itr_only", itr={"config": "itr.yaml", "workers": workers, "microbatch_size": 1,
                                  "scheduler": mode, "partition_seed": 5})
    (run_dir,) = run_experiment(cfg, cs, splits, root=root, log=lambda m: None)
    assert sorted(fake_engines["seen"]) == ["c1", "c2", "c3", "c4"]  # G5.2: each once
    rows = read_per_cluster(run_dir / "per_cluster.csv")
    assert [r.cluster_id for r in rows] == [c.cluster_id for c in cs]
    sched = json.loads((run_dir / "manifest.json").read_text())["scheduler"]
    assert (sched["mode"], sched["workers"], sched["n_tasks"]) == (mode, workers, 4)
    assert 1 <= sched["peak_concurrency"] <= workers  # G5.6
    assert (sched["partition"] is None) == (mode in ("serial", "dynamic"))
    task_rows = [t for t in read_timing(run_dir / "timing.csv") if t.kind == "itr_task"]
    assert [t.id for t in task_rows] == ["t0", "t1", "t2", "t3"]
    by_worker = {r.cluster_id: r.worker_id for r in rows if r.worker_id is not None}
    assert set(by_worker.values()) <= set(range(workers))
    if sched["partition"]:
        assert all(by_worker[f"c{int(t[1:]) + 1}"] == int(w) for w, ids in sched["partition"].items() for t in ids)


def test_warmup_runs_are_kept_but_not_returned(root, fake_engines):
    cs = adaptive_clusters()
    dirs = run_experiment(config("adaptive", warmup=1, repetitions=2), cs,
                          {c.cluster_id: "dev" for c in cs}, root=root, log=lambda m: None)
    assert [d.name[-3:] for d in dirs] == ["-r1", "-r2"]
    (warm,) = [d for d in (root / "out").iterdir() if d.name.endswith("-w1")]
    assert json.loads((warm / "manifest.json").read_text())["measured"] is False
    assert json.loads((dirs[0] / "manifest.json").read_text())["measured"] is True


def test_hyperthreaded_flag_from_physical_cores(root, fake_engines, monkeypatch):
    monkeypatch.setattr(runner.prov, "provenance",
                        lambda: {"project_dirty": False, "hardware": {"physical_cores": 2}})
    cs = adaptive_clusters()
    for workers, expected in ((2, False), (3, True)):
        cfg = config("itr_only", experiment_name=f"w{workers}",
                     itr={"config": "itr.yaml", "workers": workers, "microbatch_size": 1, "scheduler": "dynamic"})
        (run_dir,) = run_experiment(cfg, cs, {c.cluster_id: "dev" for c in cs}, root=root, log=lambda m: None)
        assert json.loads((run_dir / "manifest.json").read_text())["scheduler"]["hyperthreaded"] is expected
