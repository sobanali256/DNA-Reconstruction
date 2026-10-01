"""Run one experiment config: one method over a fixed cluster list, one folder per repetition.

    .venv/bin/python scripts/run_experiment.py configs/pilot_bbs.yaml

Each repetition writes results/<run_id>/ (run_id = <experiment>-<YYYYMMDD-HHMMSS>-r<k>):
  * manifest.json: config (with the engine settings it loaded), command, provenance
    (git hashes, dirty flag, tools, hardware), times, counts and status. Written first as
    `running`, rewritten as `complete` or `failed`; a failed run keeps its folder.
  * per_cluster.csv and timing.csv (dnarecon.results);
  * raw/: BBS input, CSV and stderr; ITR scratch files (kept only when a cluster failed).

Methods: bbs_only (shards run one after another, `threads` BBS threads each) and itr_only
(micro-batches run one after another on one worker). Adaptive runs need the router and
parallel ITR needs the scheduler; both come later and are rejected here.

A failing BBS shard or ITR task is recorded as that engine's failure for its clusters and
the run continues. Ground truth is used only by attach_metrics, after reconstruction.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

import yaml

from dnarecon import provenance as prov
from dnarecon.bbs_adapter import BbsRunError, BbsSettings, run_shard
from dnarecon.dataset import SPLIT_DEV, load_dataset, read_cluster_list, read_split_table
from dnarecon.itr_adapter import ItrRunError, ItrSettings, run_itr_batch
from dnarecon.models import ClusterRecord
from dnarecon.results import (
    TimingRecord,
    attach_metrics,
    build_record,
    timing_from_bbs_shard,
    timing_from_itr_batch,
    write_per_cluster,
    write_timing,
)

ROOT = prov.ROOT
RUNNABLE_METHODS = ("bbs_only", "itr_only")
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

Log = Callable[[str], None]


class DirtyTreeError(RuntimeError):
    """The project has uncommitted changes, so results could not be traced to a commit."""


def load_config(path: str | Path) -> dict:
    """Read and validate an experiment config."""
    cfg = yaml.safe_load(Path(path).read_text())
    for key in ("experiment_name", "dataset", "clusters", "method", "repetitions", "output_root"):
        if key not in cfg:
            raise ValueError(f"{path}: missing '{key}'")
    if not NAME_RE.match(str(cfg["experiment_name"])):
        raise ValueError(f"{path}: experiment_name may use only letters, digits, '_', '.', '-'")
    method = cfg["method"]
    if method == "adaptive":
        raise ValueError(f"{path}: adaptive runs need the router (Week 2); run bbs_only and itr_only")
    if method not in RUNNABLE_METHODS:
        raise ValueError(f"{path}: unknown method {method!r}")
    if cfg.get("split", "dev") not in ("dev", "test"):
        raise ValueError(f"{path}: split must be dev or test")
    if int(cfg["repetitions"]) < 1:
        raise ValueError(f"{path}: repetitions must be >= 1")
    if method == "bbs_only":
        bbs = cfg.get("bbs") or {}
        for key in ("config", "threads", "shards"):
            if key not in bbs:
                raise ValueError(f"{path}: missing 'bbs.{key}'")
        if int(bbs["threads"]) < 1 or int(bbs["shards"]) < 1:
            raise ValueError(f"{path}: bbs.threads and bbs.shards must be >= 1")
    else:
        itr = cfg.get("itr") or {}
        for key in ("config", "workers", "microbatch_size"):
            if key not in itr:
                raise ValueError(f"{path}: missing 'itr.{key}'")
        if int(itr["workers"]) != 1:
            raise ValueError(f"{path}: parallel ITR needs the scheduler (Week 3); use workers: 1")
        if int(itr["microbatch_size"]) < 1:
            raise ValueError(f"{path}: itr.microbatch_size must be >= 1")
    return cfg


def load_clusters(cfg: dict, root: Path = ROOT) -> tuple[list[ClusterRecord], dict[str, str]]:
    """The config's cluster list (in list order) and the split of every cluster."""
    ds = yaml.safe_load((root / cfg["dataset"]).read_text())
    by_id = {r.cluster_id: r for r in load_dataset(ds, root)}
    splits = read_split_table(root / ds["split"]["output_path"])
    ids = read_cluster_list(root / cfg["clusters"])
    if "split" in cfg:  # optional: run only the dev or the test clusters of the list
        ids = [cid for cid in ids if splits.get(cid) == cfg["split"]]
    unknown = [cid for cid in ids if cid not in by_id]
    if unknown:
        raise ValueError(f"{len(unknown)} unknown cluster IDs, e.g. {unknown[:3]}")
    if not ids:
        raise ValueError("empty cluster list")
    return [by_id[cid] for cid in ids], splits


def run_experiment(
    cfg: dict,
    clusters: Sequence[ClusterRecord],
    splits: dict[str, str],
    *,
    root: Path = ROOT,
    allow_dirty: bool = False,
    command: Sequence[str] = (),
    log: Log = print,
) -> list[Path]:
    """Run every repetition of `cfg` on `clusters`; return the run folders."""
    provenance = prov.provenance()
    if provenance["project_dirty"] and not allow_dirty:
        raise DirtyTreeError("uncommitted changes: commit first, or pass --allow-dirty")
    if len({c.cluster_id for c in clusters}) != len(clusters):
        raise ValueError("duplicate cluster IDs")
    missing = [c.cluster_id for c in clusters if c.cluster_id not in splits]
    if missing:
        raise ValueError(f"clusters without a split: {missing[:3]}")

    engine_cfg = _engine_config(cfg, root)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    reps = int(cfg["repetitions"])
    run_dirs = []
    for rep in range(1, reps + 1):
        run_id = f"{cfg['experiment_name']}-{stamp}-r{rep}"
        run_dir = root / cfg["output_root"] / run_id
        log(f"[{run_id}] {cfg['method']} on {len(clusters)} clusters (repetition {rep}/{reps})")
        _run_once(cfg, engine_cfg, clusters, splits, run_id, run_dir, rep, provenance,
                  list(command), root, log)
        run_dirs.append(run_dir)
    return run_dirs


def _engine_config(cfg: dict, root: Path) -> dict:
    section = cfg["bbs"] if cfg["method"] == "bbs_only" else cfg["itr"]
    return yaml.safe_load((root / section["config"]).read_text())


def _run_once(cfg, engine_cfg, clusters, splits, run_id, run_dir, rep, provenance,
              command, root, log) -> None:
    run_dir.mkdir(parents=True, exist_ok=False)  # never reuse a run folder
    manifest = {
        "run_id": run_id,
        "experiment_name": cfg["experiment_name"],
        "method": cfg["method"],
        "repetition": rep,
        "repetitions": int(cfg["repetitions"]),
        "command": command,
        "config": cfg,
        "engine_config": engine_cfg,
        "clusters_file_sha256": hashlib.sha256((root / cfg["clusters"]).read_bytes()).hexdigest(),
        "n_clusters": len(clusters),
        "n_empty_clusters": sum(c.is_empty for c in clusters),
        "provenance": provenance,
        "started_at": _now(),
        "ended_at": None,
        "status": "running",
        "error": None,
    }
    _write_manifest(run_dir, manifest)
    try:
        start, start_epoch = time.perf_counter(), time.time()
        if cfg["method"] == "bbs_only":
            records, timing = _run_bbs(cfg, engine_cfg, clusters, splits, run_id, run_dir, root, log)
        else:
            records, timing = _run_itr(cfg, engine_cfg, clusters, splits, run_id, run_dir, root, log)
        stage_status = next(t.status for t in timing if t.kind == "stage")
        timing.append(TimingRecord(run_id, "run", run_id, None, len(clusters), None, start_epoch,
                                   time.time(), (time.perf_counter() - start) * 1000, None,
                                   stage_status))
        scored = [attach_metrics(r, c.original_sequence) for r, c in zip(records, clusters)]
        write_per_cluster(scored, run_dir / "per_cluster.csv",
                          expected_cluster_ids=[c.cluster_id for c in clusters])
        write_timing(timing, run_dir / "timing.csv")
        manifest["status"] = "complete"
        dev = [r for r in scored if r.split == SPLIT_DEV]  # never show test accuracy
        log(f"[{run_id}] complete: {sum(r.exact_match for r in dev)}/{len(dev)} dev exact, "
            f"{sum(r.status == 'failed' for r in scored)} failed")
    except BaseException as exc:  # includes Ctrl-C: the folder must say it is not valid
        manifest["status"] = "failed"
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        manifest["ended_at"] = _now()
        _write_manifest(run_dir, manifest)


def _run_bbs(cfg, engine_cfg, clusters, splits, run_id, run_dir, root, log):
    settings = BbsSettings.from_config(engine_cfg, root)
    threads = int(cfg["bbs"]["threads"])
    non_empty = [c for c in clusters if not c.is_empty]
    lengths = {c.expected_length for c in non_empty}
    if len(lengths) > 1:
        raise ValueError(f"one BBS run needs one strand length, found {sorted(lengths)}")
    raw = run_dir / "raw"
    results, shard_of, failed, timing = {}, {}, set(), []
    stage_start, stage_epoch = time.perf_counter(), time.time()
    for i, chunk in enumerate(_chunks(non_empty, n_parts=int(cfg["bbs"]["shards"]))):
        shard_id = f"s{i}"
        for c in chunk:
            shard_of[c.cluster_id] = shard_id
        started, t0 = time.time(), time.perf_counter()
        try:
            shard = run_shard(chunk, settings, length=chunk[0].expected_length, threads=threads,
                              workdir=raw, shard_id=shard_id)
        except BbsRunError as exc:
            log(f"[{run_id}] BBS shard {shard_id} failed: {exc}")
            failed.update(c.cluster_id for c in chunk)
            timing.append(TimingRecord(run_id, "bbs_shard", shard_id, None, len(chunk), threads,
                                       started, time.time(), (time.perf_counter() - t0) * 1000,
                                       1, "failed"))
            continue
        results.update((r.cluster_id, r) for r in shard.results)
        timing.append(timing_from_bbs_shard(run_id, shard))
    timing.append(_stage(run_id, "bbs", len(non_empty), threads, stage_epoch, stage_start,
                         "failed" if failed else "ok"))
    records = [
        build_record(c, run_id=run_id, split=splits[c.cluster_id], method="bbs_only",
                     bbs=results.get(c.cluster_id), bbs_shard_id=shard_of.get(c.cluster_id, ""),
                     bbs_shard_failed=c.cluster_id in failed)
        for c in clusters
    ]
    return records, timing


def _run_itr(cfg, engine_cfg, clusters, splits, run_id, run_dir, root, log):
    settings = ItrSettings.from_config(engine_cfg, root)
    size = int(cfg["itr"]["microbatch_size"])
    worker_id = 0  # one worker until the scheduler exists
    non_empty = [c for c in clusters if not c.is_empty]
    raw = run_dir / "raw"
    results, task_of, failed, timing = {}, {}, set(), []
    stage_start, stage_epoch = time.perf_counter(), time.time()
    done = 0
    for i in range(0, len(non_empty), size):
        batch = non_empty[i:i + size]
        task_id = f"t{i // size}"
        for c in batch:
            task_of[c.cluster_id] = task_id
        started, t0 = time.time(), time.perf_counter()
        try:
            out = run_itr_batch(batch, settings, workdir=raw, task_id=task_id)
        except ItrRunError as exc:
            log(f"[{run_id}] ITR task {task_id} failed: {exc}")
            failed.update(c.cluster_id for c in batch)
            timing.append(TimingRecord(run_id, "itr_task", task_id, worker_id, len(batch), 1,
                                       started, time.time(), (time.perf_counter() - t0) * 1000,
                                       None, "failed"))
        else:
            results.update((r.cluster_id, r) for r in out.results)
            timing.append(timing_from_itr_batch(run_id, out, worker_id))
            bad = [r for r in out.results if r.itr_failed]
            if bad:
                log(f"[{run_id}] {task_id}: " + ", ".join(f"{r.cluster_id} {r.status}" for r in bad))
        done += len(batch)
        log(f"[{run_id}] ITR {done}/{len(non_empty)} clusters, "
            f"{time.perf_counter() - stage_start:.0f} s")
    timing.append(_stage(run_id, "itr", len(non_empty), 1, stage_epoch, stage_start,
                         "failed" if failed else "ok"))
    records = []
    for c in clusters:
        cid = c.cluster_id
        ran = cid in task_of
        records.append(build_record(
            c, run_id=run_id, split=splits[cid], method="itr_only",
            itr=results.get(cid), itr_task_id=task_of.get(cid, ""),
            itr_task_failed=cid in failed, worker_id=worker_id if ran else None,
        ))
    return records, timing


def _stage(run_id, stage_id, n, threads, started_at, start, status) -> TimingRecord:
    return TimingRecord(run_id, "stage", stage_id, None, n, threads, started_at, time.time(),
                        (time.perf_counter() - start) * 1000, None, status)


def _chunks(items: list, n_parts: int) -> list[list]:
    """Split into at most n_parts contiguous, near-equal, non-empty chunks."""
    n_parts = min(n_parts, len(items))
    if n_parts == 0:
        return []
    base, extra = divmod(len(items), n_parts)
    out, i = [], 0
    for k in range(n_parts):
        step = base + (k < extra)
        out.append(items[i:i + step])
        i += step
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_manifest(run_dir: Path, manifest: dict) -> None:
    tmp = run_dir / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, indent=2, default=str) + "\n")
    tmp.replace(run_dir / "manifest.json")
