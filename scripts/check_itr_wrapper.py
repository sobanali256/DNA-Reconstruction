"""Check that our ITR wrapper (external/itr_cli) reproduces upstream ITR exactly.

Upstream DNA seeds a generator from the clock but, with its default priorities, never
draws from it (docs/upstream_notes.md), so its output is deterministic and can be
compared directly. Each sampled cluster is run through the UNMODIFIED upstream binary
alone in its own file.

Checks on a sample of dev clusters:
  1. wrapper output == upstream output given the TRUE original strand
     (the wrapper sees only the length; this is also gate G2.3),
  2. upstream output is the same when the original is replaced by random letters of the
     same length (G2.3 again, from the upstream side),
  3. wrapper output does not depend on batch order or size (reversed batch, one cluster
     per call) or on the seed value (upstream ITR is deterministic).

    .venv/bin/python scripts/check_itr_wrapper.py [--n 40]
Exit code 0 only if every check passes.
"""

from __future__ import annotations

import argparse
import random
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from dnarecon.dataset import load_microsoft

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_BIN = ROOT / "external/reconstruction/Iterative/build/DNA"
CHECK_DIR = ROOT / "external/itr_check"


def run_upstream(original: str, reads, workdir: Path) -> str:
    """Run unmodified upstream DNA on a one-cluster file; return its final guess."""
    infile = workdir / "cluster.txt"
    infile.write_text(original + "\n*****\n" + "\n".join(reads) + "\n\n\n")
    subprocess.run([str(UPSTREAM_BIN), str(infile), str(workdir)], check=True, capture_output=True)
    for name in ("output-results-success.txt", "output-results-fail.txt"):
        lines = (workdir / name).read_text().splitlines()
        if lines:
            return lines[2]  # "Cluster Num", original, guess, distance
    raise RuntimeError("upstream wrote no result")


def run_wrapper(binary: Path, seed: int, records) -> dict[str, tuple[str, str]]:
    batch = "".join(
        f">{r.cluster_id} {r.expected_length} {len(r.reads)}\n" + "".join(x + "\n" for x in r.reads)
        for r in records
    )
    out = subprocess.run([str(binary), "--seed", str(seed)], input=batch, text=True,
                         capture_output=True, check=True).stdout.splitlines()
    rows = [line.split("\t") for line in out[1:]]
    assert len(rows) == len(records), "wrapper row count mismatch"
    return {row[0]: (row[1], row[5]) for row in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=40, help="number of multi-read dev clusters")
    args = parser.parse_args()

    ds = yaml.safe_load((ROOT / "configs/dataset_microsoft.yaml").read_text())
    itr = yaml.safe_load((ROOT / "configs/itr.yaml").read_text())
    wrapper = ROOT / itr["binary"]
    seed = int(itr["seed"])

    records = load_microsoft(ROOT / ds["clusters_path"], ROOT / ds["centers_path"], ds["expected_length"])
    split_rows = (ROOT / ds["split"]["output_path"]).read_text().splitlines()[1:]
    split = dict(line.split(",")[:2] for line in split_rows)
    dev = [r for r in records if split[r.cluster_id] == "dev" and r.reads]
    # Every single-read dev cluster, plus the first n multi-read ones (many have > 25 reads).
    sample = [r for r in dev if len(r.reads) == 1] + [r for r in dev if len(r.reads) > 1][: args.n]
    print(f"{len(sample)} dev clusters ({sum(len(r.reads) > 25 for r in sample)} with > 25 reads, "
          f"{sum(len(r.reads) == 1 for r in sample)} single-read), seed {seed}")

    batch = run_wrapper(wrapper, seed, sample)
    reverse = run_wrapper(wrapper, seed, sample[::-1])
    other_seed = run_wrapper(wrapper, seed + 1, sample)
    rng = random.Random(0)
    failures = 0
    CHECK_DIR.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CHECK_DIR) as tmp:
        for r in sample:
            up_true = run_upstream(r.original_sequence, r.reads, Path(tmp))
            decoy = "".join(rng.choice("ACGT") for _ in range(len(r.original_sequence)))
            up_decoy = run_upstream(decoy, r.reads, Path(tmp))
            single = run_wrapper(wrapper, seed, [r])[r.cluster_id][1]
            status, got = batch[r.cluster_id]
            checks = {
                "wrapper==upstream(true original)": got == up_true,
                "upstream(decoy)==upstream(true)": up_decoy == up_true,
                "reversed batch": reverse[r.cluster_id][1] == got,
                "one-cluster batch": single == got,
                "other seed": other_seed[r.cluster_id][1] == got,
            }
            bad = [name for name, ok in checks.items() if not ok]
            if bad:
                failures += 1
                print(f"FAIL {r.cluster_id} ({len(r.reads)} reads, {status}): {', '.join(bad)}")
    exact = sum(batch[r.cluster_id][1] == r.original_sequence for r in sample)
    print(f"exact reconstructions (info only): {exact}/{len(sample)}")
    print("ALL CHECKS PASS" if failures == 0 else f"{failures} clusters FAILED")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
