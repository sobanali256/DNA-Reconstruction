"""Build the 10-cluster fixture data/fixtures/microsoft_dev10.jsonl from the dev split.

    .venv/bin/python scripts/make_fixture.py [--check]

The selection is deterministic and uses read counts only (never ground truth or tool
output), so it covers the ITR code paths: the first dev cluster with 1 read, the first
with exactly 2 reads, the 2 dev clusters with the most reads (> 25, so the read cap
applies), and the first 6 other dev clusters in file order. The Microsoft dataset is
MIT-licensed (data/fixtures/README.md).
"""

from __future__ import annotations

import argparse
import filecmp
import sys
import tempfile
from pathlib import Path

import yaml

from dnarecon.dataset import load_microsoft, read_split_table, save_records_jsonl

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data/fixtures/microsoft_dev10.jsonl"


def select(records, split):
    dev = [r for r in records if split[r.cluster_id] == "dev" and r.reads]
    chosen = [next(r for r in dev if r.coverage == 1), next(r for r in dev if r.coverage == 2)]
    chosen += sorted(dev, key=lambda r: (-r.coverage, r.cluster_id))[:2]
    taken = {r.cluster_id for r in chosen}
    chosen += [r for r in dev if r.cluster_id not in taken and 3 <= r.coverage <= 25][:6]
    order = {r.cluster_id: i for i, r in enumerate(records)}
    return sorted(chosen, key=lambda r: order[r.cluster_id])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="verify the committed fixture")
    args = parser.parse_args()
    ds = yaml.safe_load((ROOT / "configs/dataset_microsoft.yaml").read_text())
    records = load_microsoft(ROOT / ds["clusters_path"], ROOT / ds["centers_path"], ds["expected_length"])
    fixture = select(records, read_split_table(ROOT / ds["split"]["output_path"]))
    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            save_records_jsonl(fixture, Path(tmp) / "f.jsonl")
            same = filecmp.cmp(Path(tmp) / "f.jsonl", OUTPUT, shallow=False)
        print("fixture matches" if same else "fixture DIFFERS")
        sys.exit(0 if same else 1)
    save_records_jsonl(fixture, OUTPUT)
    print(f"wrote {OUTPUT.relative_to(ROOT)}: " + ", ".join(f"{r.cluster_id}({r.coverage})" for r in fixture))


if __name__ == "__main__":
    main()
