"""Run a scaling campaign: every (scheduler, workers) cell of a config, repeated, cell order rotated.

    .venv/bin/python scripts/run_scaling.py configs/scaling_smoke.yaml

Each cell is the campaign's base adaptive config with itr.scheduler, itr.workers and
bbs.threads (= workers: one native-concurrency cap for both stages) replaced; every run is
an ordinary run folder (scripts/run_experiment.py) whose manifest config carries a
`campaign` block (name, cell, repetition, position). Before repetition 1 the first cell runs
`warmup` times unmeasured. Repetition k runs the cells rotated by k-1 positions, so slow
drift of the machine does not always hit the same cell.
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from dnarecon.runner import DirtyTreeError, load_clusters, load_config, run_experiment  # noqa: E402


def expand_cells(cells: list[dict]) -> list[tuple[str, int]]:
    """[(scheduler, workers)] in config order."""
    return [(c["scheduler"], int(w)) for c in cells for w in c["workers"]]


def cell_config(base: dict, campaign: str, scheduler: str, workers: int, **campaign_info) -> dict:
    cfg = copy.deepcopy(base)
    cfg["experiment_name"] = f"{campaign}_{scheduler}_p{workers}"
    cfg["itr"].update(scheduler=scheduler, workers=workers)
    cfg["bbs"]["threads"] = workers
    cfg.update(repetitions=1, warmup=0,
               campaign={"name": campaign, "cell": f"{scheduler}_p{workers}", **campaign_info})
    return cfg


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", type=Path)
    parser.add_argument("--allow-dirty", action="store_true", help="debugging only: results are not traceable")
    args = parser.parse_args()
    camp = yaml.safe_load(args.config.read_text())
    base = {**load_config(ROOT / camp["base"]), **(camp.get("overrides") or {})}
    if base["method"] != "adaptive":
        sys.exit("the campaign base must be an adaptive config")
    clusters, splits = load_clusters(base, ROOT)
    cells = expand_cells(camp["cells"])
    reps, name = int(camp["repetitions"]), camp["campaign"]
    command = ["scripts/run_scaling.py", str(args.config)]
    print(f"[{name}] {len(cells)} cells x {reps} repetitions on {len(clusters)} clusters, "
          f"{camp.get('warmup', 0)} warm-up run(s)")

    def log(msg: str) -> None:
        if "] ITR " not in msg:  # per-task progress would flood the campaign log
            print(msg, flush=True)

    try:
        first = cells[0]
        for _ in range(int(camp.get("warmup", 0))):
            cfg = cell_config(base, name, *first, repetition=0, position=0, measured=False)
            cfg.update(warmup=1, repetitions=0)  # one unmeasured run, no measured one
            run_experiment(cfg, clusters, splits, root=ROOT, allow_dirty=args.allow_dirty,
                           command=command, log=log)
        for rep in range(1, reps + 1):
            shift = (rep - 1) % len(cells)
            for pos, (scheduler, workers) in enumerate(cells[shift:] + cells[:shift]):
                cfg = cell_config(base, name, scheduler, workers, repetition=rep, position=pos, measured=True)
                run_experiment(cfg, clusters, splits, root=ROOT, allow_dirty=args.allow_dirty,
                               command=command, log=log)
    except DirtyTreeError as exc:
        sys.exit(f"refusing to run: {exc}")


if __name__ == "__main__":
    main()
