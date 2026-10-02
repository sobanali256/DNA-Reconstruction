"""Real BBS + ITR through the scheduler on the 10-cluster fixture (gates G5.1, G5.4, G5.5).

Equivalence compares routing and ITR output. BBS breaks score ties at random (CLAUDE.md),
so BBS sequences may differ between runs, but only where confidence <= 0.5; confidence
itself, and so routing, is stable.
"""

import json
import random
from pathlib import Path

import pytest
import yaml

from dnarecon import runner
from dnarecon.dataset import load_records_jsonl
from dnarecon.models import ClusterRecord
from dnarecon.results import read_per_cluster

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "data/fixtures/microsoft_dev10.jsonl"
BINARIES = [ROOT / "external/bbs/target/release/bbs", ROOT / "external/itr_cli"]

pytestmark = pytest.mark.skipif(not all(b.exists() for b in BINARIES),
                                reason="needs scripts/setup_external.sh")


def run(tmp_path, clusters, name, method="adaptive", itr_yaml="configs/itr.yaml", **itr):
    ids = tmp_path / f"{name}.csv"
    ids.write_text("cluster_id,n_reads\n" + "".join(f"{c.cluster_id},{c.coverage}\n" for c in clusters))
    cfg = {"experiment_name": name, "dataset": "configs/dataset_microsoft.yaml", "clusters": str(ids),
           "method": method, "repetitions": 1, "output_root": str(tmp_path / "results"),
           "itr": {"config": itr_yaml, "workers": 1, "microbatch_size": 2, **itr}}
    if method == "adaptive":
        cfg.update(tau=1.0, bbs={"config": "configs/bbs.yaml", "threads": 2, "shards": 1})
    splits = {c.cluster_id: "dev" for c in clusters}
    (run_dir,) = runner.run_experiment(cfg, clusters, splits, allow_dirty=True, log=lambda m: None)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["status"] == "complete"
    return {r.cluster_id: r for r in read_per_cluster(run_dir / "per_cluster.csv")}, manifest


@pytest.fixture(scope="module")
def clusters():
    return [*load_records_jsonl(FIXTURE),
            ClusterRecord("empty-1", (), 110, "microsoft_cnr", original_sequence="A" * 110)]


def assert_equivalent(a, b):
    assert list(a) == list(b)  # same membership and order (G5.2 across runs)
    for cid in a:
        x, y = a[cid], b[cid]
        assert (x.routed_to_itr, x.bbs_confidence) == (y.routed_to_itr, y.bbs_confidence)
        assert (x.itr_status, x.itr_sequence) == (y.itr_status, y.itr_sequence)
        if x.bbs_sequence != y.bbs_sequence:  # only a BBS tie may differ
            assert x.bbs_confidence <= 0.5


def test_g5_1_and_g5_4_schedules_give_identical_routing_and_itr_output(tmp_path, clusters):
    serial, m = run(tmp_path, clusters, "serial")
    assert m["n_routed"] == 10 and m["scheduler"]["peak_concurrency"] == 1
    routed_hash = m["routed_ids_sha256"]
    dyn1, _ = run(tmp_path, clusters, "dyn1", scheduler="dynamic")  # G5.1: serial = 1 worker
    assert_equivalent(serial, dyn1)
    for name, mode in (("dyn4a", "dynamic"), ("dyn4b", "dynamic"), ("static4", "static"),
                       ("lpt4", "static_lpt")):  # G5.4: repeated and other schedules
        rows, m = run(tmp_path, clusters, name, scheduler=mode, workers=4)
        assert_equivalent(serial, rows)
        assert m["routed_ids_sha256"] == routed_hash and m["scheduler"]["peak_concurrency"] <= 4
        assert len({r.worker_id for r in rows.values() if r.worker_id is not None}) > 1


def test_g5_5_bad_clusters_are_contained(tmp_path, clusters):
    rnd = random.Random(1)
    huge = "".join(rnd.choice("ACGT") for _ in range(3000))
    bad = [ClusterRecord("bad-chars", ("ACGTXZ" * 18,) * 2, 110, "microsoft_cnr", original_sequence="A" * 110),
           ClusterRecord("too-slow", (huge,) * 10, 110, "microsoft_cnr", original_sequence="A" * 110)]
    itr_yaml = tmp_path / "itr_fast_timeout.yaml"
    itr_yaml.write_text(yaml.safe_dump({**yaml.safe_load((ROOT / "configs/itr.yaml").read_text()),
                                        "timeout_s_per_cluster": 5}))
    data = [*clusters[:4], *bad, *clusters[4:]]
    rows, m = run(tmp_path, data, "inject", method="itr_only", itr_yaml=str(itr_yaml),
                  scheduler="dynamic", workers=4, microbatch_size=1)
    assert (rows["bad-chars"].itr_status, rows["bad-chars"].failure_reason) == ("task_failed", "itr_task_failed")
    assert rows["too-slow"].itr_status == "timeout"
    good = [r for cid, r in rows.items() if cid not in ("bad-chars", "too-slow", "empty-1")]
    assert len(good) == 10 and all(r.status == "ok" for r in good)
