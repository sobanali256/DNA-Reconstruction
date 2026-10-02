"""Unit tests for the scaling campaign helpers (scripts/run_scaling.py, analysis/scaling_summary.py)."""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "analysis"))
from run_scaling import cell_config, expand_cells  # noqa: E402
from scaling_summary import campaign_runs, cell_rows  # noqa: E402

BASE = {"experiment_name": "x", "dataset": "d.yaml", "clusters": "c.csv", "output_root": "out",
        "method": "adaptive", "tau": 0.8, "repetitions": 3,
        "bbs": {"config": "b.yaml", "threads": 4, "shards": 1},
        "itr": {"config": "i.yaml", "workers": 4, "scheduler": "dynamic", "microbatch_size": 5}}


def test_cells_expand_in_config_order():
    cells = [{"scheduler": "serial", "workers": [1]}, {"scheduler": "dynamic", "workers": [1, 2]}]
    assert expand_cells(cells) == [("serial", 1), ("dynamic", 1), ("dynamic", 2)]


def test_cell_config_sets_one_concurrency_cap_and_leaves_base_alone():
    cfg = cell_config(BASE, "camp", "static", 2, invocation="i1", repetition=1, position=0, measured=True)
    assert (cfg["itr"]["scheduler"], cfg["itr"]["workers"], cfg["bbs"]["threads"]) == ("static", 2, 2)
    assert cfg["experiment_name"] == "camp_static_p2_rep1" and cfg["repetitions"] == 1
    assert cell_config(BASE, "camp", "static", 2, repetition=0)["experiment_name"] == "camp_static_p2_warmup"
    assert cfg["campaign"]["cell"] == "static_p2" and cfg["tau"] == 0.8
    assert BASE["itr"]["workers"] == 4 and BASE["bbs"]["threads"] == 4  # deep copy


def run(cell, scheduler, p, makespan, itr):
    return {"cell": cell, "scheduler": scheduler, "workers": p, "hyperthreaded": p > 4,
            "makespan_s": makespan, "itr_s": itr, "bbs_s": 1.0, "utilization": 0.9,
            "imbalance": 1.1, "peak_concurrency": p, "itr_timeouts": 0}


def test_speedup_and_efficiency_use_the_serial_median():
    runs = pd.DataFrame([run("serial_p1", "serial", 1, m, m - 1) for m in (100, 101, 120)]
                        + [run("dynamic_p4", "dynamic", 4, m, m - 1) for m in (25, 26, 30)])
    cells = cell_rows(runs).set_index("cell")
    assert cells.loc["serial_p1", "speedup"] == 1
    assert cells.loc["dynamic_p4", "speedup"] == pytest.approx(101 / 26)
    assert cells.loc["dynamic_p4", "efficiency"] == pytest.approx(101 / 26 / 4)
    assert cells.loc["dynamic_p4", "itr_speedup"] == pytest.approx(100 / 25)
    assert (cells.loc["dynamic_p4", "makespan_s_min"], cells.loc["dynamic_p4", "makespan_s_max"]) == (25, 30)


def test_invalid_cell_is_rejected_before_running():
    with pytest.raises(ValueError, match="cell serial_p2"):
        cell_config(BASE, "camp", "serial", 2, repetition=1)


def test_summary_uses_one_invocation_only(tmp_path):
    def manifest(run_id, invocation, status="complete", measured=True):
        d = tmp_path / run_id
        d.mkdir()
        camp = {"name": "camp", "invocation": invocation, "measured": measured}
        (d / "manifest.json").write_text(json.dumps(
            {"run_id": run_id, "status": status, "measured": measured, "config": {"campaign": camp}}))
        (d / "timing.csv").write_text("kind,status\nstage,ok\nrun," + ("failed" if run_id == "b4" else "ok") + "\n")

    manifest("a1", "20261002-100000")
    manifest("b1", "20261002-120000")
    manifest("b2", "20261002-120000", status="failed")
    manifest("b3", "20261002-120000", measured=False)
    manifest("b4", "20261002-120000")  # a stage failed (e.g. a BBS shard): workload changed
    manifest("other", "20261002-120000")
    (tmp_path / "other" / "manifest.json").write_text(json.dumps({"run_id": "other", "config": {}}))
    used, skipped, inv = campaign_runs(tmp_path, "camp", None)
    assert inv == "20261002-120000" and [m["run_id"] for m in used] == ["b1"]
    assert len(skipped) == 4  # a1 (older launch), b2 (failed), b3 (warm-up), b4 (failed stage)
    used, _, inv = campaign_runs(tmp_path, "camp", "20261002-100000")
    assert [m["run_id"] for m in used] == ["a1"]
