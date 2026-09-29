"""Run the real ITR wrapper through the adapter on the 10-cluster fixture (G2.3, G2.4, G5.7)."""

from pathlib import Path

import pytest
import yaml

from dnarecon.dataset import load_records_jsonl
from dnarecon.itr_adapter import ItrSettings, run_itr_batch

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ItrSettings.from_config(yaml.safe_load((ROOT / "configs/itr.yaml").read_text()), ROOT)
FIXTURE = ROOT / "data/fixtures/microsoft_dev10.jsonl"

pytestmark = pytest.mark.skipif(not SETTINGS.binary.exists(), reason="needs scripts/setup_external.sh")


@pytest.fixture(scope="module")
def records():
    return load_records_jsonl(FIXTURE)


@pytest.fixture(scope="module")
def whole_batch(records, tmp_path_factory):
    return run_itr_batch(records, SETTINGS, workdir=tmp_path_factory.mktemp("itr"), task_id="all")


def test_g24_every_cluster_gets_a_result(records, whole_batch):
    assert [r.cluster_id for r in whole_batch.results] == [r.cluster_id for r in records]
    assert all(r.status in {"ok", "single_read"} for r in whole_batch.results)
    assert all(len(r.sequence) > 0 for r in whole_batch.results)
    by_id = {r.cluster_id: r for r in whole_batch.results}
    one_read = next(r for r in records if r.coverage == 1)
    assert by_id[one_read.cluster_id].sequence == one_read.reads[0]
    assert all(res.reads_used == min(rec.coverage, 25) for res, rec in zip(whole_batch.results, records))


def test_g23_no_ground_truth_in_wrapper_input(records, tmp_path):
    # Force a failure so the input file is kept, then inspect it.
    settings = ItrSettings(SETTINGS.binary, SETTINGS.seed, timeout_s_per_cluster=1e-6)
    run_itr_batch(records[:2], settings, workdir=tmp_path, task_id="g23")
    text = "".join(p.read_text() for p in tmp_path.glob("g23.itr.input*.txt"))
    assert text
    assert all(r.original_sequence not in text for r in records)


def test_g57_micro_batch_size_does_not_change_results(records, whole_batch, tmp_path):
    singles = [run_itr_batch([r], SETTINGS, workdir=tmp_path, task_id=f"one{i}").results[0]
               for i, r in enumerate(records)]
    fives = [res for i in (0, 5) for res in
             run_itr_batch(records[i:i + 5], SETTINGS, workdir=tmp_path, task_id=f"five{i}").results]
    key = lambda rs: [(r.cluster_id, r.status, r.sequence) for r in rs]
    assert key(singles) == key(whole_batch.results) == key(fives)
