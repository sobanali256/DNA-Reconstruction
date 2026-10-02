"""Run a scaling campaign: every (scheduler, workers) cell of a config, repeated, cell order rotated.

    .venv/bin/python scripts/run_scaling.py configs/scaling_smoke.yaml

Each cell is the campaign's base adaptive config with itr.scheduler, itr.workers and
bbs.threads (= workers: one native-concurrency cap for both stages) replaced. `overrides`
replace whole top-level keys of the base. Every cell config is validated before the first
run. Every run is an ordinary run folder (scripts/run_experiment.py) named
<campaign>_<cell>_rep<k>-<stamp>-r1, whose manifest config carries a `campaign` block (name,
invocation = this launch's start time, cell, repetition, position, measured); the summary
uses one invocation only. Before repetition 1 the first cell runs `warmup` times unmeasured.
Repetition k runs the cells rotated by k-1 positions, so slow drift of the machine does not
always hit the same cell.
"""

from __future__ import annotations

import argparse
import copy
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from dnarecon.runner import (  # noqa: E402
    DirtyTreeError, load_clusters, load_config, run_experiment, validate_config,
)


def expand_cells(cells: list[dict]) -> list[tuple[str, int]]:
    """[(scheduler, workers)] in config order."""
    return [(c["scheduler"], int(w)) for c in cells for w in c["workers"]]


def campaign_base(camp: dict, root: Path = ROOT) -> dict:
    """The campaign's base adaptive config with its top-level `overrides` applied."""
    return {**load_config(root / camp["base"]), **(camp.get("overrides") or {})}


def cell_config(base: dict, campaign: str, scheduler: str, workers: int, **campaign_info) -> dict:
    cfg = copy.deepcopy(base)
    rep = campaign_info.get("repetition", 0)
    cfg["experiment_name"] = f"{campaign}_{scheduler}_p{workers}_" + (f"rep{rep}" if rep else "warmup")
    cfg["itr"].update(scheduler=scheduler, workers=workers)
    cfg["bbs"]["threads"] = workers
    cfg.update(repetitions=1, warmup=0,
               campaign={"name": campaign, "cell": f"{scheduler}_p{workers}", **campaign_info})
    return validate_config(cfg, f"cell {scheduler}_p{workers}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", type=Path)
    parser.add_argument("--allow-dirty", action="store_true", help="debugging only: results are not traceable")
    args = parser.parse_args()
    camp = yaml.safe_load(args.config.read_text())
    base = validate_config(campaign_base(camp), f"{camp['base']} + overrides")
    if base["method"] != "adaptive":
        sys.exit("the campaign base must be an adaptive config")
    cells = expand_cells(camp["cells"])
    reps, name = int(camp["repetitions"]), camp["campaign"]
    invocation = time.strftime("%Y%m%d-%H%M%S")
    for cell in cells:  # every cell is checked before anything runs
        cell_config(base, name, *cell, invocation=invocation, repetition=1, position=0, measured=True)
    clusters, splits = load_clusters(base, ROOT)
    command = ["scripts/run_scaling.py", str(args.config)]
    print(f"[{name}] invocation {invocation}: {len(cells)} cells x {reps} repetitions on "
          f"{len(clusters)} clusters, {camp.get('warmup', 0)} warm-up run(s)")

    def log(msg: str) -> None:
        if "] ITR " not in msg:  # per-task progress would flood the campaign log
            print(msg, flush=True)

    try:
        if int(camp.get("warmup", 0)):
            cfg = cell_config(base, name, *cells[0], invocation=invocation, repetition=0, position=0,
                              measured=False)
            cfg.update(warmup=int(camp["warmup"]), repetitions=0)  # -w1..-wN, no measured run
            run_experiment(cfg, clusters, splits, root=ROOT, allow_dirty=args.allow_dirty,
                           command=command, log=log)
        for rep in range(1, reps + 1):
            shift = (rep - 1) % len(cells)
            for pos, (scheduler, workers) in enumerate(cells[shift:] + cells[:shift]):
                cfg = cell_config(base, name, scheduler, workers, invocation=invocation,
                                  repetition=rep, position=pos, measured=True)
                run_experiment(cfg, clusters, splits, root=ROOT, allow_dirty=args.allow_dirty,
                               command=command, log=log)
    except DirtyTreeError as exc:
        sys.exit(f"refusing to run: {exc}")


if __name__ == "__main__":
    main()
