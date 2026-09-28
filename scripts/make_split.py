"""Load the Microsoft CNR dataset, apply the eligibility rule and write the dev/test split.

    .venv/bin/python scripts/make_split.py configs/dataset_microsoft.yaml

Writes data/splits/microsoft_cnr_split.csv (committed). Re-running with the same
config must reproduce the file byte for byte; use --check to verify that.
"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import sys
import tempfile
from collections import Counter
from pathlib import Path

import yaml

from dnarecon.dataset import build_split_table, load_microsoft, write_split_table

ROOT = Path(__file__).resolve().parent.parent


def verify_checksums(checksums_path: Path, data_dir: Path) -> None:
    for line in checksums_path.read_text().splitlines():
        expected, name = line.split()
        actual = hashlib.sha256((data_dir / name).read_bytes()).hexdigest()
        if actual != expected:
            sys.exit(f"Checksum mismatch for {name}: run scripts/download_microsoft.sh again")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", type=Path)
    parser.add_argument("--check", action="store_true",
                        help="regenerate into a temp file and compare with the committed split")
    args = parser.parse_args()

    cfg = yaml.safe_load(args.config.read_text())
    clusters_path = ROOT / cfg["clusters_path"]
    verify_checksums(ROOT / cfg["checksums_path"], clusters_path.parent)

    records = load_microsoft(clusters_path, ROOT / cfg["centers_path"],
                             cfg["expected_length"], cfg["dataset_id"])
    if len(records) != cfg["expected_clusters"]:
        sys.exit(f"Expected {cfg['expected_clusters']} clusters, found {len(records)}")

    split_cfg = cfg["split"]
    rows = build_split_table(records, split_cfg["dev_fraction"], split_cfg["seed"])
    output = ROOT / split_cfg["output_path"]

    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "split.csv"
            write_split_table(rows, fresh)
            if not filecmp.cmp(fresh, output, shallow=False):
                sys.exit(f"MISMATCH: {output} differs from a fresh regeneration")
        print(f"OK: {output} reproduces exactly")
        return

    write_split_table(rows, output)
    counts = Counter((r.split, r.engine_input) for r in rows)
    print(f"Wrote {output}")
    for split in ("dev", "test"):
        total = counts[(split, True)] + counts[(split, False)]
        print(f"  {split:4s}: {total:5d} clusters ({counts[(split, False)]} empty)")
    print(f"  reads: {sum(r.n_reads for r in rows)}")


if __name__ == "__main__":
    main()
