"""Unit tests for the pilot sample: dev clusters only, reproducible, in file order."""

import subprocess
import sys
from pathlib import Path

import pytest

from dnarecon.dataset import draw_pilot, read_cluster_list, write_pilot

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def split_file(tmp_path):
    path = tmp_path / "split.csv"
    lines = ["cluster_id,split,n_reads,eligible,engine_input,note"]
    for i in range(200):
        split = "dev" if i % 3 == 0 else "test"
        lines.append(f"c{i:04d},{split},{i % 7},1,{int(i % 7 > 0)},")
    path.write_text("\n".join(lines) + "\n")
    return path


def test_pilot_is_dev_only_sorted_and_reproducible(split_file):
    rows = draw_pilot(split_file, 30, seed=5)
    ids = [cid for cid, _ in rows]
    assert len(ids) == len(set(ids)) == 30
    assert all(int(cid[1:]) % 3 == 0 for cid in ids)  # dev clusters only
    assert ids == sorted(ids)  # file order
    assert all(n == int(cid[1:]) % 7 for cid, n in rows)
    assert draw_pilot(split_file, 30, seed=5) == rows
    assert draw_pilot(split_file, 30, seed=6) != rows


@pytest.mark.parametrize("size", [0, 68])  # 67 dev clusters in the file
def test_pilot_size_must_fit(split_file, size):
    with pytest.raises(ValueError):
        draw_pilot(split_file, size, seed=5)


def test_write_and_read_cluster_list(split_file, tmp_path):
    rows = draw_pilot(split_file, 10, seed=1)
    write_pilot(rows, tmp_path / "p.csv")
    assert read_cluster_list(tmp_path / "p.csv") == [cid for cid, _ in rows]
    (tmp_path / "dup.csv").write_text("cluster_id,n_reads\na,1\na,1\n")
    with pytest.raises(ValueError):
        read_cluster_list(tmp_path / "dup.csv")


def test_committed_pilot_reproduces():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/make_pilot.py"),
                             str(ROOT / "configs/dataset_microsoft.yaml"), "--check"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
