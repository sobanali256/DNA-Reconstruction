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


def test_itr_only_base_gets_a_cell_without_bbs():
    base = {k: v for k, v in BASE.items() if k not in ("bbs", "tau")} | {"method": "itr_only"}
    cfg = cell_config(base, "camp", "dynamic", 2, repetition=1)
    assert (cfg["itr"]["scheduler"], cfg["itr"]["workers"]) == ("dynamic", 2) and "bbs" not in cfg


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
    def manifest(run_id, invocation, cell="serial_p1", status="complete", measured=True, dirty=False,
                 run_status="ok"):
        d = tmp_path / run_id
        d.mkdir()
        camp = {"name": "camp", "invocation": invocation, "cell": cell, "measured": measured}
        (d / "manifest.json").write_text(json.dumps(
            {"run_id": run_id, "status": status, "measured": measured, "config": {"campaign": camp},
             "provenance": {"project_dirty": dirty}}))
        (d / "timing.csv").write_text(f"kind,status\nstage,{run_status}\nrun,{run_status}\n")

    expected = {"serial_p1": 1, "dynamic_p2": 1}
    manifest("a1", "20261002-100000")                       # complete launch
    manifest("a2", "20261002-100000", cell="dynamic_p2")
    manifest("b1", "20261002-120000")                       # later launch, aborted after one run
    manifest("b2", "20261002-120000", cell="dynamic_p2", status="failed")
    manifest("b3", "20261002-120000", measured=False)
    manifest("b4", "20261002-120000", cell="dynamic_p2", run_status="failed")  # a stage failed
    manifest("c1", "20261002-130000", dirty=True)           # --allow-dirty launch
    manifest("c2", "20261002-130000", cell="dynamic_p2", dirty=True)
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "manifest.json").write_text(json.dumps({"run_id": "x", "config": {}}))  # other campaign

    used, skipped, inv = campaign_runs(tmp_path, "camp", None, expected)
    assert inv == "20261002-100000" and sorted(m["run_id"] for m in used) == ["a1", "a2"]  # latest complete
    why = dict(s.split(" ", 1) for s in skipped)
    assert "failed" in why["b2"] and "warm-up" in why["b3"] and "stage failed" in why["b4"]
    assert "dirty" in why["c1"] and "other invocation" in why["b1"]
    used, _, inv = campaign_runs(tmp_path, "camp", "20261002-120000", expected)
    assert [m["run_id"] for m in used] == ["b1"]  # explicit choice, incomplete (main stops on G8.1)
