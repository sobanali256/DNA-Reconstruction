"""Run the real BBS binary through the adapter on a few Microsoft clusters."""

from pathlib import Path

import pytest
import yaml

from dnarecon.bbs_adapter import BbsSettings, run_shard
from dnarecon.dataset import load_microsoft

ROOT = Path(__file__).resolve().parents[2]
DS = yaml.safe_load((ROOT / "configs/dataset_microsoft.yaml").read_text())
SETTINGS = BbsSettings.from_config(yaml.safe_load((ROOT / "configs/bbs.yaml").read_text()), ROOT)

pytestmark = pytest.mark.skipif(
    not SETTINGS.binary.exists() or not (ROOT / DS["clusters_path"]).exists(),
    reason="needs scripts/setup_external.sh and scripts/download_microsoft.sh",
)


def test_bbs_shard_maps_rows_to_clusters(tmp_path):
    records = load_microsoft(ROOT / DS["clusters_path"], ROOT / DS["centers_path"], DS["expected_length"])
    sample = [r for r in records if r.reads][:20]
    shard = run_shard(sample, SETTINGS, length=DS["expected_length"], threads=1,
                      workdir=tmp_path, shard_id="t")
    assert [r.cluster_id for r in shard.results] == [r.cluster_id for r in sample]
    assert shard.wall_time_s > 0 and "-t" in shard.command
    exact = sum(res.sequence == rec.original_sequence for res, rec in zip(shard.results, sample))
    assert exact >= 15  # BBS is ~95% exact on this dataset
    assert all(0.0 <= res.confidence <= 1.0 for res in shard.results)
