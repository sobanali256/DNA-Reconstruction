"""Real BBS + ITR through the adaptive method on the 10-cluster fixture (+ one empty cluster).

At τ = 1 every cluster with confidence < 1 goes to ITR; its ITR output must equal an
ITR-only run on the same clusters (ITR is deterministic), and routing must follow the
confidences of a BBS-only run (confidence is stable even when BBS breaks ties). G3.4, G3.5.
"""

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


def test_adaptive_matches_single_engine_runs(tmp_path):
    clusters = [*load_records_jsonl(FIXTURE),
                ClusterRecord("empty-1", (), 110, "microsoft_cnr", original_sequence="A" * 110)]
    splits = {c.cluster_id: "dev" for c in clusters}
    ids = tmp_path / "list.csv"
    ids.write_text("cluster_id,n_reads\n" + "".join(f"{c.cluster_id},{c.coverage}\n" for c in clusters))
    common = {"dataset": "configs/dataset_microsoft.yaml", "clusters": str(ids),
              "repetitions": 1, "output_root": str(tmp_path / "results")}
    bbs = {"config": "configs/bbs.yaml", "threads": 2, "shards": 1}
    itr = {"config": "configs/itr.yaml", "workers": 1, "microbatch_size": 4}
    cfgs = {
        "bbs": {**common, "experiment_name": "fx_bbs", "method": "bbs_only", "bbs": bbs},
        "itr": {**common, "experiment_name": "fx_itr", "method": "itr_only", "itr": itr},
        "ada": {**common, "experiment_name": "fx_ada", "method": "adaptive", "tau": 1.0,
                "bbs": bbs, "itr": itr},
    }
    rows = {}
    for name, cfg in cfgs.items():
        (run_dir,) = runner.run_experiment(cfg, clusters, splits, allow_dirty=True, log=lambda m: None)
        rows[name] = {r.cluster_id: r for r in read_per_cluster(run_dir / "per_cluster.csv")}

    ada = rows["ada"]
    assert list(ada) == [c.cluster_id for c in clusters]  # G3.5
    for cid, r in ada.items():
        assert r.final_algorithm in ("bbs", "itr", "none") and r.routed_to_itr is not None  # G3.4
        conf = rows["bbs"][cid].bbs_confidence
        assert r.bbs_confidence == conf
        assert r.routed_to_itr == (conf is not None and conf < 1.0)
        if r.routed_to_itr:
            assert r.itr_sequence == rows["itr"][cid].itr_sequence
            assert r.final_sequence == (r.itr_sequence if not r.itr_failed else r.bbs_sequence)
        else:
            assert r.final_sequence == r.bbs_sequence
    assert ada["empty-1"].failure_reason == "empty_cluster"
