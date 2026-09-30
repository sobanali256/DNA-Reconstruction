"""Real BBS + ITR on the 10-cluster fixture (plus one empty cluster) -> records -> CSV files."""

from pathlib import Path

import pytest
import yaml

from dnarecon.bbs_adapter import BbsSettings, run_shard
from dnarecon.dataset import load_records_jsonl
from dnarecon.itr_adapter import ItrSettings, run_itr_batch
from dnarecon.models import ClusterRecord
from dnarecon.results import (
    attach_metrics,
    build_record,
    read_per_cluster,
    read_timing,
    timing_from_bbs_shard,
    timing_from_itr_batch,
    write_per_cluster,
    write_timing,
)

ROOT = Path(__file__).resolve().parents[2]
BBS = BbsSettings.from_config(yaml.safe_load((ROOT / "configs/bbs.yaml").read_text()), ROOT)
ITR = ItrSettings.from_config(yaml.safe_load((ROOT / "configs/itr.yaml").read_text()), ROOT)
FIXTURE = ROOT / "data/fixtures/microsoft_dev10.jsonl"

pytestmark = pytest.mark.skipif(not (BBS.binary.exists() and ITR.binary.exists()),
                                reason="needs scripts/setup_external.sh")


def test_adaptive_run_writes_consistent_files(tmp_path):
    records = load_records_jsonl(FIXTURE)
    empty = ClusterRecord("empty-1", (), 110, "microsoft_cnr", original_sequence="A" * 110)
    clusters = [*records, empty]

    shard = run_shard(records, BBS, length=110, threads=1, workdir=tmp_path, shard_id="s0")
    bbs_by_id = {r.cluster_id: r for r in shard.results}
    # Route an arbitrary half: this test is about the records, not the threshold.
    routed = [r for r in records if bbs_by_id[r.cluster_id].confidence < 1.0][:5] or records[:5]
    batch = run_itr_batch(routed, ITR, workdir=tmp_path, task_id="t0")
    itr_by_id = {r.cluster_id: r for r in batch.results}

    rows = []
    for c in clusters:
        itr = itr_by_id.get(c.cluster_id)
        row = build_record(
            c, run_id="it", split="dev", method="adaptive",
            bbs=bbs_by_id.get(c.cluster_id), bbs_shard_id="s0",
            itr=itr, itr_task_id="t0" if itr else "", worker_id=0 if itr else None,
        )
        rows.append(attach_metrics(row, c.original_sequence))

    out = tmp_path / "results" / "it"
    write_per_cluster(rows, out / "per_cluster.csv", expected_cluster_ids=[c.cluster_id for c in clusters])
    write_timing([timing_from_bbs_shard("it", shard), timing_from_itr_batch("it", batch, 0)],
                 out / "timing.csv")

    back = read_per_cluster(out / "per_cluster.csv")
    assert back == rows
    assert sum(r.routed_to_itr for r in back) == len(routed)
    assert {r.final_algorithm for r in back if r.routed_to_itr} == {"itr"}
    assert back[-1].failure_reason == "empty_cluster" and back[-1].edit_distance == 110
    assert all(r.status == "ok" for r in back[:-1])
    # The truth may appear only as a correct reconstruction, never as a column of its own.
    text = (out / "per_cluster.csv").read_text()
    assert "original_sequence" not in text
    for c, r in zip(clusters, back):
        if c.original_sequence not in (r.bbs_sequence, r.itr_sequence, r.final_sequence):
            assert c.original_sequence not in text

    timing = read_timing(out / "timing.csv")
    assert [t.kind for t in timing] == ["bbs_shard", "itr_task"]
    assert timing[0].started_at <= timing[0].ended_at and timing[0].n_clusters == 10
