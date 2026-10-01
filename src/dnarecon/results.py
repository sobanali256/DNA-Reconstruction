"""Per-cluster result records and run timing files (design doc v3, section 3.4).

A run writes two files to results/<run_id>/:
  * per_cluster.csv: one ResultRecord per eligible cluster, empty clusters included;
  * timing.csv: one TimingRecord per BBS shard, ITR task, stage and run. BBS has no
    per-cluster times; its timing exists only here, per shard.

Records are built in two steps so that ground truth cannot influence routing or the
final selection: `build_record` uses engine outputs only, then `attach_metrics` adds the
quality columns. Written files are raw results: the writers refuse to overwrite.

Final selection (decided 30 Sep 2026): a successful ITR result wins; otherwise the BBS
result is kept (so an ITR timeout or crash on a routed cluster falls back to BBS); with
neither, the cluster fails. An empty BBS string still counts as an output.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, fields, replace
from pathlib import Path
from types import NoneType
from typing import Iterable, Sequence, get_args, get_origin, get_type_hints

from dnarecon.bbs_adapter import BbsClusterResult, BbsShardResult
from dnarecon.itr_adapter import ItrBatchResult, ItrClusterResult
from dnarecon.metrics import score_cluster
from dnarecon.models import ClusterRecord, ResultRecord

METHODS = ("bbs_only", "itr_only", "adaptive")
NO_ENGINE_STATUSES = frozenset({"not_run", "not_routed"})  # engine never saw the cluster


def build_record(
    cluster: ClusterRecord,
    *,
    run_id: str,
    split: str,
    method: str,
    bbs: BbsClusterResult | None = None,
    bbs_shard_id: str = "",
    bbs_shard_failed: bool = False,
    itr: ItrClusterResult | None = None,
    itr_task_id: str = "",
    itr_task_failed: bool = False,
    worker_id: int | None = None,
) -> ResultRecord:
    """Combine one cluster's engine outputs into a ResultRecord (no quality columns yet).

    In an adaptive run the cluster counts as routed to ITR exactly when `itr` is given or
    `itr_task_failed` is set. `bbs_shard_failed` / `itr_task_failed` mark a cluster whose
    BBS shard / ITR task failed as a whole (BbsRunError / ItrRunError): no per-cluster
    result exists, and the cluster is recorded as that engine's failure.
    """
    if method not in METHODS:
        raise ValueError(f"unknown method {method!r}")
    uses_bbs = method != "itr_only"
    uses_itr = method != "bbs_only"
    cid = cluster.cluster_id
    if bbs is not None and (not uses_bbs or bbs.cluster_id != cid):
        raise ValueError(f"{cid}: unexpected BBS result for method {method}")
    if itr is not None and (not uses_itr or itr.cluster_id != cid):
        raise ValueError(f"{cid}: unexpected ITR result for method {method}")
    if bbs_shard_failed and not uses_bbs:
        raise ValueError(f"{cid}: failed BBS shard given for method {method}")
    if itr_task_failed and (itr is not None or not uses_itr):
        raise ValueError(f"{cid}: failed ITR task given with an ITR result or method {method}")
    ran_itr = itr is not None or itr_task_failed
    if not ran_itr and (itr_task_id or worker_id is not None):
        raise ValueError(f"{cid}: ITR task/worker given without an ITR result")

    if cluster.is_empty:
        if bbs is not None or ran_itr or bbs_shard_failed:
            raise ValueError(f"{cid}: empty clusters are never sent to an engine")
        bbs_status = "empty_cluster" if uses_bbs else "not_run"
        itr_status = "empty_cluster" if uses_itr else "not_run"
    else:
        if not uses_bbs:
            bbs_status = "not_run"
        elif bbs_shard_failed:
            if bbs is not None:
                raise ValueError(f"{cid}: BBS result given for a failed shard")
            bbs_status = "shard_failed"
        elif bbs is None:
            raise ValueError(f"{cid}: missing BBS result")
        else:
            bbs_status = "ok"
        if method == "itr_only" and not ran_itr:
            raise ValueError(f"{cid}: missing ITR result")
        if method == "adaptive" and bbs_shard_failed and ran_itr:
            raise ValueError(f"{cid}: cannot route without a BBS confidence")
        if itr is not None:
            itr_status = itr.status
        elif itr_task_failed:
            itr_status = "task_failed"
        else:
            itr_status = "not_routed" if method == "adaptive" else "not_run"

    itr_ok = itr is not None and not itr.itr_failed
    if itr_ok:
        final_sequence, final_algorithm = itr.sequence, "itr"
    elif bbs is not None:
        final_sequence, final_algorithm = bbs.sequence, "bbs"
    else:
        final_sequence, final_algorithm = None, "none"

    if final_sequence is not None:
        failure_reason = ""
    elif cluster.is_empty:
        failure_reason = "empty_cluster"
    elif bbs_status == "shard_failed":
        failure_reason = "bbs_shard_failed"
    elif itr_task_failed:
        failure_reason = "itr_task_failed"
    else:  # ITR-only run and ITR failed
        failure_reason = f"itr_{itr.status}" + (f": {itr.failure_reason}" if itr.failure_reason else "")

    return ResultRecord(
        run_id=run_id,
        split=split,
        cluster_id=cid,
        dataset_id=cluster.dataset_id,
        eligible_for_eval=True,  # every cluster is eligible (docs/data_policy.md)
        method=method,
        coverage=cluster.coverage,
        expected_length=cluster.expected_length,
        error_profile=cluster.error_profile,
        bbs_status=bbs_status,
        bbs_shard_id=bbs_shard_id if uses_bbs and not cluster.is_empty else "",
        bbs_sequence=bbs.sequence if bbs else None,
        bbs_k=bbs.k if bbs else None,
        bbs_path_weight=bbs.path_weight if bbs else None,
        bbs_confidence=bbs.confidence if bbs else None,
        routed_to_itr=ran_itr,
        itr_status=itr_status,
        itr_task_id=itr_task_id,
        worker_id=worker_id,
        itr_sequence=itr.sequence if itr_ok else None,
        itr_failed=True if itr_task_failed else (itr.itr_failed if itr else None),
        itr_reads_used=itr.reads_used if itr else None,
        itr_runtime_ms=itr.runtime_ms if itr else None,
        final_sequence=final_sequence,
        final_algorithm=final_algorithm,
        total_cluster_runtime_ms=itr.runtime_ms if method == "itr_only" and itr else None,
        status="ok" if final_sequence is not None else "failed",
        failure_reason=failure_reason,
    )


def attach_metrics(record: ResultRecord, truth: str | None) -> ResultRecord:
    """Add the quality columns. The only place where ground truth meets a result.

    Final, BBS and ITR are each scored over every cluster the respective engine was
    responsible for; a missing output scores as an empty prediction (dnarecon.metrics).
    """
    if record.exact_match is not None:
        raise ValueError(f"{record.cluster_id}: metrics already attached")
    final = score_cluster(record.final_sequence, truth)
    bbs = None if record.bbs_status in NO_ENGINE_STATUSES else score_cluster(record.bbs_sequence, truth)
    itr = None if record.itr_status in NO_ENGINE_STATUSES else score_cluster(record.itr_sequence, truth)
    return replace(
        record,
        exact_match=final.exact_match,
        edit_distance=final.edit_distance,
        normalized_edit_distance=final.normalized_edit_distance,
        hamming_distance=final.hamming_distance,
        bbs_exact_match=bbs.exact_match if bbs else None,
        bbs_edit_distance=bbs.edit_distance if bbs else None,
        bbs_hamming_distance=bbs.hamming_distance if bbs else None,
        itr_exact_match=itr.exact_match if itr else None,
        itr_edit_distance=itr.edit_distance if itr else None,
        itr_hamming_distance=itr.hamming_distance if itr else None,
    )


@dataclass(frozen=True)
class TimingRecord:
    """One row of results/<run_id>/timing.csv.

    kind: bbs_shard | itr_task | stage | run. Makespan is the `run` row; orchestration
    overhead is derived in analysis. started_at/ended_at are epoch seconds; wall_time_ms
    comes from a monotonic clock where available.
    """

    run_id: str
    kind: str
    id: str
    worker_id: int | None
    n_clusters: int
    threads: int | None
    started_at: float | None
    ended_at: float | None
    wall_time_ms: float
    launches: int | None
    status: str  # ok | cluster_failures (some ITR clusters failed) | failed


TIMING_KINDS = ("bbs_shard", "itr_task", "stage", "run")


def timing_from_bbs_shard(run_id: str, shard: BbsShardResult) -> TimingRecord:
    return TimingRecord(run_id, "bbs_shard", shard.shard_id, None, len(shard.results),
                        shard.threads, shard.started_at, shard.ended_at,
                        shard.wall_time_s * 1000, 1, "ok")


def timing_from_itr_batch(run_id: str, batch: ItrBatchResult, worker_id: int | None) -> TimingRecord:
    status = "cluster_failures" if any(r.itr_failed for r in batch.results) else "ok"
    return TimingRecord(run_id, "itr_task", batch.task_id, worker_id, len(batch.results), 1,
                        batch.started_at, batch.ended_at, batch.wall_time_s * 1000,
                        batch.launches, status)


def write_per_cluster(records: Sequence[ResultRecord], path: str | Path,
                      *, expected_cluster_ids: Iterable[str]) -> None:
    """Write per_cluster.csv: exactly one row per expected cluster, one run, no overwrite."""
    ids = [r.cluster_id for r in records]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate cluster IDs")
    expected = set(expected_cluster_ids)
    if set(ids) != expected:
        missing, extra = expected - set(ids), set(ids) - expected
        raise ValueError(f"cluster IDs differ: {len(missing)} missing, {len(extra)} unexpected")
    if len({r.run_id for r in records}) > 1:
        raise ValueError("records from more than one run")
    _write_rows(records, ResultRecord, Path(path))


def read_run(run_dir: str | Path) -> tuple[dict, list[ResultRecord], list[TimingRecord]]:
    """Manifest, per-cluster records and timing of one complete run folder."""
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    if manifest["status"] != "complete":
        raise ValueError(f"{run_dir}: run status is {manifest['status']}, not complete")
    return manifest, read_per_cluster(run_dir / "per_cluster.csv"), read_timing(run_dir / "timing.csv")


def read_per_cluster(path: str | Path) -> list[ResultRecord]:
    records = _read_rows(ResultRecord, Path(path))
    # A blank cell cannot tell "" from None; the status columns can.
    return [
        replace(
            r,
            bbs_sequence="" if r.bbs_status == "ok" and r.bbs_sequence is None else r.bbs_sequence,
            itr_sequence="" if r.itr_failed is False and r.itr_sequence is None else r.itr_sequence,
            final_sequence="" if r.status == "ok" and r.final_sequence is None else r.final_sequence,
        )
        for r in records
    ]


def write_timing(records: Sequence[TimingRecord], path: str | Path) -> None:
    bad = {r.kind for r in records} - set(TIMING_KINDS)
    if bad:
        raise ValueError(f"unknown timing kinds: {sorted(bad)}")
    _write_rows(records, TimingRecord, Path(path))


def read_timing(path: str | Path) -> list[TimingRecord]:
    return _read_rows(TimingRecord, Path(path))


def _write_rows(rows: Sequence, cls: type, path: Path) -> None:
    names = [f.name for f in fields(cls)]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", newline="") as handle:  # "x": raw results are never overwritten
        writer = csv.writer(handle)
        writer.writerow(names)
        for row in rows:
            writer.writerow([_format(getattr(row, name)) for name in names])


def _read_rows(cls: type, path: Path) -> list:
    hints = get_type_hints(cls)
    names = [f.name for f in fields(cls)]
    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != names:
            raise ValueError(f"{path}: unexpected header")
        return [cls(**{n: _parse(row[n], hints[n]) for n in names}) for row in reader]


def _format(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _parse(text: str, hint):
    args = get_args(hint)
    nullable = NoneType in args
    base = next((a for a in args if a is not NoneType), hint) if nullable else hint
    if text == "" and nullable:
        return None
    base = get_origin(base) or base
    if base is bool:
        if text not in ("True", "False"):
            raise ValueError(f"not a boolean: {text!r}")
        return text == "True"
    if base is int:
        return int(text)
    if base is float:
        return float(text)
    if base is dict:
        return json.loads(text)
    return text
