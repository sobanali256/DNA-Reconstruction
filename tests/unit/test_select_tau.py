"""Unit tests for analysis/select_tau.py: the τ rule and the dev-only guard (gate G7.5)."""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "analysis"))
from select_tau import check_dev_run, select_tau  # noqa: E402

SWEEP = pd.DataFrame({
    "tau": [0, 0.5, 0.8, 0.9, 1.0],
    "exact_rate": [0.70, 0.85, 0.888, 0.890, 0.886],
    "fallback_ratio": [0.0, 0.2, 0.3, 0.4, 1.0],
})


def test_cheapest_tau_within_tolerance_of_the_best():
    chosen = select_tau(SWEEP, 0.0025)
    assert chosen.tau == 0.8 and chosen.best_tau == 0.9 and chosen.best_exact_rate == 0.890


def test_zero_tolerance_takes_the_best():
    assert select_tau(SWEEP, 0).tau == 0.9


def test_exact_tie_on_the_boundary_is_eligible():
    sweep = SWEEP.assign(exact_rate=[0.70, 0.85, 0.8875, 0.890, 0.886])  # exactly best - 0.0025
    assert select_tau(sweep, 0.0025).tau == 0.8


def test_bbs_only_when_nothing_beats_it():
    sweep = SWEEP.assign(exact_rate=[0.96, 0.958, 0.95, 0.94, 0.88])
    assert select_tau(sweep, 0.0025).tau == 0


@pytest.mark.parametrize("split,ok", [("dev", True), ("test", False), (None, False)])
def test_only_dev_runs_are_accepted(tmp_path, split, ok):
    (tmp_path / "manifest.json").write_text(json.dumps({"config": {"split": split}}))
    if ok:
        check_dev_run(tmp_path)
    else:
        with pytest.raises(SystemExit):
            check_dev_run(tmp_path)
