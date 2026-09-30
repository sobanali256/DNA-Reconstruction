"""Draw the pilot sample: a fixed random subset of the dev split.

    .venv/bin/python scripts/make_pilot.py configs/dataset_microsoft.yaml

Writes data/splits/microsoft_pilot1000.csv (committed): `cluster_id,n_reads`, in file
order. It reads only the split file, so it never sees ground truth or test clusters.
Use --check to verify that the committed file reproduces byte for byte.
"""

from __future__ import annotations

import argparse
import filecmp
import sys
import tempfile
from pathlib import Path

import yaml

from dnarecon.dataset import draw_pilot, write_pilot

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", type=Path)
    parser.add_argument("--check", action="store_true",
                        help="regenerate into a temp file and compare with the committed sample")
    args = parser.parse_args()

    cfg = yaml.safe_load(args.config.read_text())
    pilot = cfg["pilot"]
    rows = draw_pilot(ROOT / cfg["split"]["output_path"], pilot["size"], pilot["seed"])
    output = ROOT / pilot["output_path"]

    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "pilot.csv"
            write_pilot(rows, fresh)
            if not filecmp.cmp(fresh, output, shallow=False):
                sys.exit(f"MISMATCH: {output} differs from a fresh regeneration")
        print(f"OK: {output} reproduces exactly")
        return

    write_pilot(rows, output)
    reads = [n for _, n in rows]
    print(f"Wrote {output}: {len(rows)} dev clusters, {reads.count(0)} empty, "
          f"{sum(reads)} reads (median {sorted(reads)[len(reads) // 2]})")


if __name__ == "__main__":
    main()
