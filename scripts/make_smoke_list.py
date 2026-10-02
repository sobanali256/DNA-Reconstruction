"""Draw the scaling smoke-test cluster list: a fixed random sample per synthetic condition, dev only.

    .venv/bin/python scripts/make_smoke_list.py configs/scaling_smoke.yaml

The condition of a cluster is its record's dataset_id (data/synthetic/grid.jsonl); the split
comes from the split file. Conditions are sampled in sorted order, each from its dev
clusters in split-file order. Writes the config's `output_path` in the pilot-list format
(cluster_id, n_reads), in split-file order. No ground truth is read into the list.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from dnarecon.dataset import SPLIT_DEV, load_dataset, write_pilot  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", type=Path)
    cfg = yaml.safe_load(parser.parse_args().config.read_text())["sample"]
    condition = {r.cluster_id: r.dataset_id
                 for r in load_dataset(yaml.safe_load((ROOT / cfg["dataset"]).read_text()), ROOT)}
    with open(ROOT / cfg["split_file"], newline="") as handle:
        dev = [(row["cluster_id"], int(row["n_reads"])) for row in csv.DictReader(handle)
               if row["split"] == SPLIT_DEV]
    by_condition = defaultdict(list)
    for row in dev:
        by_condition[condition[row[0]]].append(row)
    rng = np.random.default_rng(cfg["seed"])
    chosen = set()
    for cond in sorted(by_condition):
        rows = by_condition[cond]
        chosen.update(rows[i] for i in rng.choice(len(rows), size=cfg["per_condition"], replace=False))
    write_pilot([row for row in dev if row in chosen], ROOT / cfg["output_path"])
    print(f"Wrote {len(chosen)} dev clusters ({len(by_condition)} conditions) to {cfg['output_path']}")


if __name__ == "__main__":
    main()
