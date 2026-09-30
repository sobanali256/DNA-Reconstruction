"""Run an experiment config: one method, a fixed cluster list, one folder per repetition.

    .venv/bin/python scripts/run_experiment.py configs/pilot_bbs.yaml

Refuses to run with uncommitted changes (results must trace to a commit) unless
--allow-dirty is given; the manifest records the dirty flag either way.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dnarecon.runner import DirtyTreeError, load_clusters, load_config, run_experiment


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", type=Path)
    parser.add_argument("--allow-dirty", action="store_true",
                        help="run even with uncommitted changes (marked in the manifest)")
    args = parser.parse_args()

    cfg = load_config(args.config)
    clusters, splits = load_clusters(cfg)
    try:
        run_dirs = run_experiment(cfg, clusters, splits, allow_dirty=args.allow_dirty,
                                  command=sys.argv, log=lambda m: print(m, flush=True))
    except DirtyTreeError as exc:
        sys.exit(str(exc))
    print("Run folders:")
    for d in run_dirs:
        print(f"  {d}")


if __name__ == "__main__":
    main()
