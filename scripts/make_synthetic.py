"""Generate the synthetic grid: every condition's dev and test clusters, from the config alone.

    .venv/bin/python scripts/make_synthetic.py configs/dataset_synthetic.yaml

Writes data/synthetic/grid.jsonl (normalized records, gitignored) and
data/splits/synthetic_grid_split.csv (committed), then prints per condition the clusters,
reads per cluster (gate G6.3) and the observed read error = mean edit distance / length
(gate G6.2). Re-running reproduces both files byte for byte; --check verifies that.
"""

from __future__ import annotations

import argparse
import filecmp
import sys
import tempfile
from pathlib import Path

import edlib
import yaml

from dnarecon.dataset import EMPTY_CLUSTER, SplitRow, save_records_jsonl, write_split_table
from dnarecon.simulator import simulate

ROOT = Path(__file__).resolve().parent.parent


def conditions(cfg: dict) -> list[dict]:
    grid = [{"name": f"e{round(e * 100):02d}_c{c:02d}", "total_error_rate": e, "coverage": c, "hp_shorten": 0.0}
            for e in cfg["total_error_rates"] for c in cfg["coverages"]]
    return grid + cfg.get("extra_conditions", [])


def generate(cfg: dict):
    records, rows = [], []
    for cond in conditions(cfg):
        p = cond["total_error_rate"] / 3
        for split in ("dev", "test"):
            batch = simulate(cond["name"], split, cfg[f"n_{split}"], cond["coverage"], cfg["expected_length"],
                             p, p, p, cond["hp_shorten"], cfg["seed"])
            records += batch
            rows += [SplitRow(r.cluster_id, split, r.coverage, True, not r.is_empty,
                              EMPTY_CLUSTER if r.is_empty else "") for r in batch]
    return records, rows


def write(records, rows, records_path: Path, split_path: Path) -> None:
    save_records_jsonl(records, records_path)
    write_split_table(rows, split_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", type=Path)
    parser.add_argument("--check", action="store_true",
                        help="regenerate into a temp folder and compare with the existing files")
    args = parser.parse_args()
    cfg = yaml.safe_load(args.config.read_text())
    records, rows = generate(cfg)
    outputs = ROOT / cfg["records_path"], ROOT / cfg["split"]["output_path"]

    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "r.jsonl", Path(tmp) / "s.csv"
            write(records, rows, *fresh)
            for new, old in zip(fresh, outputs):
                if not filecmp.cmp(new, old, shallow=False):
                    sys.exit(f"MISMATCH: {old} differs from a fresh regeneration")
        print(f"OK: {outputs[0]} and {outputs[1]} reproduce exactly")
        return

    write(records, rows, *outputs)
    print(f"Wrote {outputs[0]} and {outputs[1]}")
    print(f"{'condition':14s} {'split':5s} {'clusters':>8s} {'reads/cluster':>13s} {'observed error':>14s}")
    for cond in conditions(cfg):
        for split in ("dev", "test"):
            batch = [r for r in records if r.dataset_id == f"synthetic_{cond['name']}" and r.cluster_id.split("-")[2] == split]
            coverages = sorted({r.coverage for r in batch})
            errors = [edlib.align(read, r.original_sequence)["editDistance"] / r.expected_length
                      for r in batch for read in r.reads]
            print(f"{cond['name']:14s} {split:5s} {len(batch):8d} {str(coverages):>13s} {sum(errors) / len(errors):14.4f}")


if __name__ == "__main__":
    main()
