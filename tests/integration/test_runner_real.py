"""Real BBS + ITR through the runner on the 10-cluster fixture (+ one empty cluster),
then the pilot summary on the resulting run folders."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from dnarecon import runner
from dnarecon.dataset import load_records_jsonl
from dnarecon.models import ClusterRecord
from dnarecon.results import read_per_cluster, read_timing

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "data/fixtures/microsoft_dev10.jsonl"
BINARIES = [ROOT / "external/bbs/target/release/bbs", ROOT / "external/itr_cli"]

pytestmark = pytest.mark.skipif(not all(b.exists() for b in BINARIES),
                                reason="needs scripts/setup_external.sh")


def test_pilot_pipeline_on_fixture(tmp_path):
    records = load_records_jsonl(FIXTURE)
    clusters = [*records, ClusterRecord("empty-1", (), 110, "microsoft_cnr", original_sequence="A" * 110)]
    splits = {c.cluster_id: "dev" for c in clusters}
    ids = tmp_path / "list.csv"
    ids.write_text("cluster_id,n_reads\n" + "".join(f"{c.cluster_id},{c.coverage}\n" for c in clusters))
    common = {"dataset": "configs/dataset_microsoft.yaml", "clusters": str(ids),
              "repetitions": 1, "output_root": str(tmp_path / "results")}
    bbs_cfg = {**yaml.safe_load((ROOT / "configs/pilot_bbs.yaml").read_text()), **common,
               "experiment_name": "fx_bbs", "repetitions": 2}
    itr_cfg = {**yaml.safe_load((ROOT / "configs/pilot_itr.yaml").read_text()), **common,
               "experiment_name": "fx_itr"}
    itr_cfg["itr"] = {**itr_cfg["itr"], "microbatch_size": 4}

    bbs_dirs = runner.run_experiment(bbs_cfg, clusters, splits, allow_dirty=True, log=lambda m: None)
    (itr_dir,) = runner.run_experiment(itr_cfg, clusters, splits, allow_dirty=True, log=lambda m: None)

    for d in [*bbs_dirs, itr_dir]:
        manifest = json.loads((d / "manifest.json").read_text())
        assert manifest["status"] == "complete" and manifest["provenance"]["bbs_commit"]
        rows = read_per_cluster(d / "per_cluster.csv")
        assert [r.cluster_id for r in rows] == [c.cluster_id for c in clusters]
        assert rows[-1].failure_reason == "empty_cluster"
        assert all(r.status == "ok" for r in rows[:-1])
    itr_timing = read_timing(itr_dir / "timing.csv")
    assert [t.kind for t in itr_timing] == ["itr_task"] * 3 + ["stage", "run"]  # 10 clusters / 4

    def summarize(bbs, out):
        return subprocess.run(
            [sys.executable, str(ROOT / "analysis/pilot_summary.py"), *map(str, bbs),
             "--itr", str(itr_dir), "--output", str(out)],
            capture_output=True, text=True,
        )

    # Only the complete set of repetitions of one invocation is accepted; order does not matter.
    reordered = summarize(bbs_dirs[::-1], tmp_path / "reordered.csv")
    assert reordered.returncode == 0, reordered.stderr
    assert f"bbs_canonical_run,{bbs_dirs[0].name}" in (tmp_path / "reordered.csv").read_text()
    assert summarize(bbs_dirs[1:], tmp_path / "x.csv").returncode != 0  # repetition 1 missing
    assert summarize(bbs_dirs[:1] + [itr_dir], tmp_path / "x.csv").returncode != 0
    more = runner.run_experiment({**bbs_cfg, "experiment_name": "fx_bbs2", "repetitions": 1}, clusters, splits,
                                 allow_dirty=True, log=lambda m: None)
    assert summarize([bbs_dirs[0], *more], tmp_path / "x.csv").returncode != 0

    out = tmp_path / "summary.csv"
    result = summarize(bbs_dirs, out)
    assert result.returncode == 0, result.stderr
    summary = {(r.split(",")[0], r.split(",")[1]): r.split(",")[2] for r in out.read_text().splitlines()[1:]}
    assert summary[("bbs", "n_clusters")] == summary[("itr", "n_clusters")] == "11"
    assert summary[("bbs", "n_empty_clusters")] == "1"
    assert summary[("bbs_time", "repetitions")] == "2"
    assert summary[("bbs_variability", "cluster_comparisons_skipped_shard_failed")] == "0"

    # Day 4 cascade analysis on the same run folders.
    cfg = {**yaml.safe_load((ROOT / "configs/pilot_cascade.yaml").read_text()),
           "bbs_run": str(bbs_dirs[0]), "fallback_run": str(itr_dir), "output_prefix": str(tmp_path / "fx"),
           "bootstrap": {"n_resamples": 50, "seed": 1}}
    (tmp_path / "cascade.yaml").write_text(yaml.safe_dump(cfg))
    result = subprocess.run([sys.executable, str(ROOT / "analysis/cascade_from_cache.py"),
                             str(tmp_path / "cascade.yaml")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    cascade = {r.split(",")[1]: r.split(",")[2] for r in (tmp_path / "fx_cascade.csv").read_text().splitlines()[1:]}
    assert sum(int(cascade[f"bbs_{b}_fallback_{i}"]) for b in ("right", "wrong") for i in ("right", "wrong")) == 11
    sweep = (tmp_path / "fx_tau_sweep.csv").read_text().splitlines()
    n_exact_bbs = sum(r.exact_match for r in read_per_cluster(bbs_dirs[0] / "per_cluster.csv"))
    assert sweep[1].split(",")[:6] == ["0.0000", "default", "11", "0", "0.0000", str(n_exact_bbs)]

    # A BBS-only run can stand in as the fallback (e.g. a wider beam); it has no per-cluster time.
    (tmp_path / "cascade_bbs.yaml").write_text(yaml.safe_dump({**cfg, "fallback_run": str(bbs_dirs[1]),
                                                               "output_prefix": str(tmp_path / "fb")}))
    result = subprocess.run([sys.executable, str(ROOT / "analysis/cascade_from_cache.py"),
                             str(tmp_path / "cascade_bbs.yaml")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    header, *rows = (tmp_path / "fb_tau_sweep.csv").read_text().splitlines()
    assert header.endswith("fallback_seconds") and all(r.endswith(",") for r in rows)  # nan = blank
