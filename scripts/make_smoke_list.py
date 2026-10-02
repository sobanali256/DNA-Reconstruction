"""Draw the scaling smoke-test cluster list: a fixed random sample per synthetic condition, dev only.

    .venv/bin/python scripts/make_smoke_list.py configs/scaling_smoke.yaml

Reads only the split file (cluster IDs, split labels, read counts); the condition is the
middle of the synthetic cluster ID (syn-<condition>-<split>-<n>). Writes the config's
`clusters` path in the pilot-list format (cluster_id, n_reads), in split-file order.
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
from dnarecon.dataset import SPLIT_DEV, write_pilot  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", type=Path)
    cfg = yaml.safe_load(parser.parse_args().config.read_text())["sample"]
    by_condition = defaultdict(list)
    with open(ROOT / cfg["split_file"], newline="") as handle:
        for row in csv.DictReader(handle):
            if row["split"] == SPLIT_DEV:
                by_condition[row["cluster_id"].rsplit("-", 2)[0].removeprefix("syn-")].append(
                    (row["cluster_id"], int(row["n_reads"])))
    rng = np.random.default_rng(cfg["seed"])
    chosen = set()
    for condition in sorted(by_condition):
        rows = by_condition[condition]
        chosen.update(rows[i] for i in rng.choice(len(rows), size=cfg["per_condition"], replace=False))
    ordered = [r for cond in by_condition.values() for r in cond if r in chosen]
    write_pilot(ordered, ROOT / cfg["output_path"])
    print(f"Wrote {len(ordered)} dev clusters ({len(by_condition)} conditions) to {cfg['output_path']}")


if __name__ == "__main__":
    main()
