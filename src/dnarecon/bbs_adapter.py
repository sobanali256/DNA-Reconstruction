"""Run BBS on a shard of clusters and map its CSV back to cluster IDs.

BBS runs in batch mode: one process per shard, never one per cluster. Its verbose CSV
(`read_id,reconstruction_result,k,path_weight,confidence`) has no cluster IDs, and it
writes no row for an empty cluster while `read_id` counts only non-empty ones
(docs/upstream_notes.md). So the adapter writes only non-empty clusters, in a known
order, and maps rows back by position after checking the row count and `read_id`s.

Timing is per shard only (wall time of the BBS process). BBS does not report
per-cluster times and we never invent them.

Ground truth never reaches this module: it writes `record.reads` and nothing else.
"""

from __future__ import annotations

import csv
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from dnarecon.models import ClusterRecord

CSV_HEADER = ["read_id", "reconstruction_result", "k", "path_weight", "confidence"]
SEPARATOR = "==="  # BBS microsoft-format default (-s)


@dataclass(frozen=True)
class BbsSettings:
    binary: Path
    beam_width: int = 20
    k_min: int = 4
    k_max: int = 62
    alpha: float = 1

    @classmethod
    def from_config(cls, config: dict, root: Path) -> "BbsSettings":
        return cls(
            binary=root / config["binary"],
            beam_width=int(config["beam_width"]),
            k_min=int(config["k_min"]),
            k_max=int(config["k_max"]),
            alpha=config["alpha"],
        )


@dataclass(frozen=True)
class BbsClusterResult:
    cluster_id: str
    sequence: str  # may be empty: BBS returns "" when it finds no candidate
    k: int
    path_weight: float
    confidence: float


@dataclass(frozen=True)
class BbsShardResult:
    shard_id: str
    results: list[BbsClusterResult]  # same order as the input records
    wall_time_s: float
    threads: int
    command: list[str]


class BbsRunError(RuntimeError):
    """BBS failed for a whole shard (crash, timeout or unusable output)."""


def write_microsoft_input(records: Sequence[ClusterRecord], path: Path) -> None:
    """Write clusters in BBS's microsoft format: a separator line, then one read per line."""
    with open(path, "w", encoding="ascii") as handle:
        for record in records:
            handle.write(SEPARATOR + "\n")
            for read in record.reads:
                handle.write(read + "\n")


def build_command(settings: BbsSettings, input_path: Path, output_path: Path,
                  length: int, threads: int) -> list[str]:
    return [
        str(settings.binary), str(input_path),
        "--format", "microsoft",
        "-l", str(length),
        "-b", str(settings.beam_width),
        "-k", str(settings.k_min),
        "-K", str(settings.k_max),
        "-a", str(settings.alpha),
        "-t", str(threads),
        "-o", str(output_path),
    ]


def parse_output(path: Path, cluster_ids: Sequence[str]) -> list[BbsClusterResult]:
    """Parse BBS's verbose CSV; row i belongs to cluster_ids[i]."""
    with open(path, newline="", encoding="ascii") as handle:
        rows = list(csv.reader(handle))
    if not rows or rows[0] != CSV_HEADER:
        raise BbsRunError(f"{path}: unexpected header {rows[0] if rows else None}")
    body = rows[1:]
    if len(body) != len(cluster_ids):
        raise BbsRunError(f"{path}: {len(body)} rows for {len(cluster_ids)} clusters")
    results = []
    for position, (row, cluster_id) in enumerate(zip(body, cluster_ids), start=1):
        if len(row) != 5 or row[0] != str(position):
            raise BbsRunError(f"{path}: row {position} is malformed or out of order: {row}")
        results.append(BbsClusterResult(
            cluster_id=cluster_id,
            sequence=row[1],
            k=int(row[2]),
            path_weight=float(row[3]),
            confidence=float(row[4]),
        ))
    return results


def run_shard(
    records: Sequence[ClusterRecord],
    settings: BbsSettings,
    *,
    length: int,
    threads: int,
    workdir: Path,
    shard_id: str,
    timeout_s: float | None = None,
) -> BbsShardResult:
    """Run BBS once on `records` and return one result per record, in input order.

    Empty clusters must be filtered out by the caller (docs/data_policy.md); passing one
    is an error. The input file, CSV and stderr log are kept in `workdir` as raw outputs.
    """
    if threads < 1:
        raise ValueError("threads must be >= 1 (BBS defaults to all logical CPUs otherwise)")
    if not records:
        raise ValueError("empty shard")
    empty = [r.cluster_id for r in records if r.is_empty]
    if empty:
        raise ValueError(f"empty clusters must not be sent to BBS: {empty[:5]}")

    workdir.mkdir(parents=True, exist_ok=True)
    input_path = workdir / f"{shard_id}.input.txt"
    output_path = workdir / f"{shard_id}.bbs.csv"
    log_path = workdir / f"{shard_id}.bbs.stderr.log"
    output_path.unlink(missing_ok=True)  # never parse a stale CSV
    write_microsoft_input(records, input_path)
    command = build_command(settings, input_path, output_path, length, threads)

    start = time.perf_counter()
    try:
        with open(log_path, "w") as log:
            proc = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=log, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise BbsRunError(f"shard {shard_id}: BBS timed out after {timeout_s} s") from exc
    wall_time_s = time.perf_counter() - start

    if proc.returncode != 0:
        raise BbsRunError(f"shard {shard_id}: BBS exited with {proc.returncode}, see {log_path}")
    if not output_path.exists():
        raise BbsRunError(f"shard {shard_id}: BBS wrote no CSV, see {log_path}")
    results = parse_output(output_path, [r.cluster_id for r in records])
    return BbsShardResult(shard_id, results, wall_time_s, threads, command)
