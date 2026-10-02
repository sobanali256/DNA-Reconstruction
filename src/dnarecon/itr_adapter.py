"""Run the ITR wrapper (external/itr_cli) on a micro-batch of clusters.

One call = one scheduler task. The wrapper reconstructs clusters one after another and
prints one TSV row per cluster as soon as it is done, so the adapter reads rows as they
arrive and gives every cluster its own deadline:

  * timeout: no row within `timeout_s_per_cluster` -> the process is killed, the running
    cluster is recorded as `timeout`, and the clusters after it continue in a fresh
    process;
  * crash: the process dies before a cluster's row -> that cluster is `crashed`, and the
    rest continue in a fresh process.

A failing cluster is never retried: ITR is deterministic, and the design doc records
algorithmic failures instead of retrying them. Problems that are not about one cluster
(the binary cannot start, malformed output, the wrapper rejecting our input) raise
ItrRunError.

Ground truth never reaches the wrapper: the input holds cluster ID, expected length and
reads only (gate G2.3). Calls share no state, so the scheduler can run several at once
from different threads as long as each task has its own task_id.
"""

from __future__ import annotations

import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from dnarecon.models import ClusterRecord

OUTPUT_HEADER = ["cluster_id", "status", "reads_in", "reads_used", "runtime_ms", "sequence"]
SUCCESS_STATUSES = frozenset({"ok", "single_read"})
WRAPPER_STATUSES = frozenset({"ok", "single_read", "error"})  # empty_cluster is never sent
TIMEOUT = "timeout"
CRASHED = "crashed"
EXIT_MALFORMED_INPUT = 2  # itr_cli's exit code for input it rejects
EXIT_GRACE_S = 10.0  # time allowed for the wrapper to exit after its last row
DNA = frozenset("ACGT")


@dataclass(frozen=True)
class ItrSettings:
    binary: Path
    seed: int
    timeout_s_per_cluster: float

    @classmethod
    def from_config(cls, config: dict, root: Path) -> "ItrSettings":
        return cls(
            binary=root / config["binary"],
            seed=int(config["seed"]),
            timeout_s_per_cluster=float(config["timeout_s_per_cluster"]),
        )


@dataclass(frozen=True)
class ItrClusterResult:
    cluster_id: str
    status: str  # ok | single_read | error | timeout | crashed
    sequence: str  # empty unless the status is a success
    runtime_ms: float  # wrapper's per-cluster time; for timeout/crash, time until detected
    reads_in: int
    reads_used: int | None  # None when the wrapper never reported the cluster
    failure_reason: str = ""

    @property
    def itr_failed(self) -> bool:
        return self.status not in SUCCESS_STATUSES


@dataclass(frozen=True)
class ItrBatchResult:
    task_id: str
    results: list[ItrClusterResult]  # same order as the input records
    started_at: float  # epoch seconds
    ended_at: float
    wall_time_s: float
    launches: int  # wrapper processes started (1 + one per timeout/crash that left work)
    command: list[str]  # command of the first launch


class ItrRunError(RuntimeError):
    """The task failed as a whole (not a single cluster's reconstruction failure)."""


def write_input(records: Sequence[ClusterRecord], path: Path) -> None:
    """Write the wrapper's input: header line per cluster, then its reads. No ground truth."""
    with open(path, "w", encoding="ascii") as handle:
        for r in records:
            handle.write(f">{r.cluster_id} {r.expected_length} {len(r.reads)}\n")
            for read in r.reads:
                handle.write(read + "\n")


def parse_row(line: str, expected: ClusterRecord) -> ItrClusterResult:
    fields = line.rstrip("\n").split("\t")
    if len(fields) != len(OUTPUT_HEADER):
        raise ItrRunError(f"malformed wrapper row: {line!r}")
    cluster_id, status, reads_in, reads_used, runtime_ms, sequence = fields
    try:
        n_in, n_used, runtime = int(reads_in), int(reads_used), float(runtime_ms)
    except ValueError as exc:
        raise ItrRunError(f"malformed wrapper row: {line!r}") from exc
    if cluster_id != expected.cluster_id:
        raise ItrRunError(f"wrapper returned {cluster_id!r}, expected {expected.cluster_id!r}")
    if status not in WRAPPER_STATUSES:
        raise ItrRunError(f"{cluster_id}: unexpected status {status!r}")
    if n_in != len(expected.reads):
        raise ItrRunError(f"{cluster_id}: wrapper saw {reads_in} reads, sent {len(expected.reads)}")
    if not set(sequence) <= DNA or (status in SUCCESS_STATUSES) != bool(sequence):
        raise ItrRunError(f"{cluster_id}: status {status!r} with sequence {sequence!r}")
    return ItrClusterResult(
        cluster_id=cluster_id,
        status=status,
        sequence=sequence,
        runtime_ms=runtime,
        reads_in=n_in,
        reads_used=n_used,
        failure_reason="wrapper reported an exception (see stderr log)" if status == "error" else "",
    )


def _pump(stream, lines: queue.Queue) -> None:
    """Reader thread: forward every stdout line, then None at end of stream."""
    for line in stream:
        lines.put(line)
    lines.put(None)


def _failed(record: ClusterRecord, status: str, runtime_ms: float, reason: str) -> ItrClusterResult:
    return ItrClusterResult(record.cluster_id, status, "", runtime_ms, len(record.reads), None, reason)


def _run_once(pending: list[ClusterRecord], settings: ItrSettings, input_path: Path,
              log, timeout_s: float) -> tuple[list[ItrClusterResult], list[str]]:
    """Start one wrapper process on `pending`. Returns the results obtained, ending with
    a timeout/crash record if the process stopped early, and the command used."""
    write_input(pending, input_path)
    command = [str(settings.binary), "--seed", str(settings.seed), str(input_path)]
    try:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log, text=True,
                                encoding="ascii", bufsize=1)
    except OSError as exc:
        raise ItrRunError(f"cannot start {settings.binary}: {exc}") from exc
    lines: queue.Queue = queue.Queue()
    reader = threading.Thread(target=_pump, args=(proc.stdout, lines), daemon=True)
    reader.start()
    results: list[ItrClusterResult] = []

    def stop() -> int:
        """Kill the process if it still runs; reap it, finish the reader, close the pipe."""
        if proc.poll() is None:
            proc.kill()
        returncode = proc.wait()
        reader.join()
        proc.stdout.close()
        return returncode

    try:
        window_start = time.monotonic()
        # Process start and the header count toward the first cluster's deadline.
        try:
            header = lines.get(timeout=timeout_s)
        except queue.Empty:
            stop()
            elapsed = (time.monotonic() - window_start) * 1000
            return [_failed(pending[0], TIMEOUT, elapsed, f"no result within {timeout_s:g} s")], command
        if header is None or header.rstrip("\n").split("\t") != OUTPUT_HEADER:
            returncode = stop()
            raise ItrRunError(f"wrapper gave no valid header (exit {returncode}): {header!r}")

        for record in pending:
            remaining = timeout_s - (time.monotonic() - window_start)
            try:
                line = lines.get(timeout=max(remaining, 0.0))
            except queue.Empty:
                stop()
                elapsed = (time.monotonic() - window_start) * 1000
                results.append(_failed(record, TIMEOUT, elapsed,
                                       f"no result within {timeout_s:g} s"))
                return results, command
            if line is None:  # process ended before this cluster's row
                returncode = stop()
                if returncode == EXIT_MALFORMED_INPUT:
                    raise ItrRunError(f"wrapper rejected the input {input_path} (exit 2)")
                elapsed = (time.monotonic() - window_start) * 1000
                results.append(_failed(record, CRASHED, elapsed,
                                       f"wrapper exited with {returncode} during this cluster"))
                return results, command
            results.append(parse_row(line, record))
            window_start = time.monotonic()

        try:
            extra = lines.get(timeout=EXIT_GRACE_S)
            proc.wait(timeout=EXIT_GRACE_S)
        except (queue.Empty, subprocess.TimeoutExpired) as exc:
            stop()
            raise ItrRunError("wrapper did not exit after its last row") from exc
        returncode = stop()
    except BaseException:
        stop()
        raise
    if extra is not None:
        raise ItrRunError(f"wrapper printed more rows than clusters: {extra!r}")
    expected_code = 1 if any(r.status == "error" for r in results) else 0
    if returncode != expected_code:
        raise ItrRunError(f"wrapper exited with {returncode} after all rows (expected {expected_code})")
    return results, command


def run_itr_batch(
    records: Sequence[ClusterRecord],
    settings: ItrSettings,
    *,
    workdir: Path,
    task_id: str,
    timeout_s_per_cluster: float | None = None,
    cancel: threading.Event | None = None,
) -> ItrBatchResult:
    """Reconstruct `records` with ITR; return exactly one result per record, in order.

    `cancel` (set by the scheduler when the run is aborted, e.g. Ctrl-C) stops the batch
    before the next wrapper launch: ItrRunError instead of relaunching for the rest.

    Empty clusters must be filtered out by the caller (docs/data_policy.md). Scratch
    files (input per launch, stderr log) go in `workdir` and are deleted when every
    cluster succeeded; they are kept when anything failed, for inspection.
    """
    if not records:
        raise ValueError("empty micro-batch")
    empty = [r.cluster_id for r in records if r.is_empty]
    if empty:
        raise ValueError(f"empty clusters must not be sent to ITR: {empty[:5]}")
    if len({r.cluster_id for r in records}) != len(records):
        raise ValueError("duplicate cluster IDs in micro-batch")
    timeout_s = settings.timeout_s_per_cluster if timeout_s_per_cluster is None else timeout_s_per_cluster

    workdir.mkdir(parents=True, exist_ok=True)
    log_path = workdir / f"{task_id}.itr.stderr.log"
    inputs: list[Path] = []
    results: list[ItrClusterResult] = []
    first_command: list[str] = []
    started_at = time.time()
    start = time.perf_counter()
    with open(log_path, "w") as log:
        pending = list(records)
        while pending:
            if cancel is not None and cancel.is_set():
                raise ItrRunError(f"{task_id}: cancelled, {len(pending)} clusters not run")
            inputs.append(workdir / f"{task_id}.itr.input{len(inputs) + 1}.txt")
            got, command = _run_once(pending, settings, inputs[-1], log, timeout_s)
            first_command = first_command or command
            results.extend(got)
            pending = pending[len(got):]
    wall_time_s = time.perf_counter() - start

    if all(not r.itr_failed for r in results):
        for path in [*inputs, log_path]:
            path.unlink(missing_ok=True)
    return ItrBatchResult(task_id, results, started_at, time.time(), wall_time_s,
                          len(inputs), first_command)
