"""Run independent tasks (ITR micro-batches) on a pool of worker threads (design doc v3, Phase 5).

Each worker thread launches native processes and waits on them, so threads are enough: the
GIL is not held while a native ITR process runs. Modes (decided 2 Oct 2026):
  * serial      one worker, tasks in list order, on the calling thread;
  * static      tasks shuffled with a fixed seed, then cut into equal contiguous blocks, one
                block per worker, fixed before the run starts (OpenMP schedule(static));
  * static_lpt  fixed before the run: longest first by a label-free cost estimate, each task
                to the least-loaded worker so far (a labeled extra baseline);
  * dynamic     one shared queue in list order; each worker pulls the next task when idle.

A worker's tasks run one at a time, so at most `workers` native tasks run at once; the
measured peak is returned so every run can show it (gate G5.6). An exception listed in
`catch` is recorded in that task's outcome and the other tasks continue (G5.5); any other
exception stops the workers taking new tasks and is re-raised; so does an exception in the
controller thread (Ctrl-C). `stop` lets the task function see that too (e.g. to skip
relaunching a native process).
"""

from __future__ import annotations

import heapq
import queue
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

MODES = ("serial", "static", "static_lpt", "dynamic")


@dataclass(frozen=True)
class Task:
    task_id: str
    items: Sequence[Any]
    cost: float = 1.0  # label-free estimate, used only by static_lpt


@dataclass(frozen=True)
class TaskOutcome:
    task_id: str
    worker_id: int
    result: Any  # fn's return value, or None when `error` is set
    error: BaseException | None
    started_at: float  # epoch seconds
    ended_at: float
    wall_ms: float


@dataclass
class ScheduleInfo:
    mode: str
    workers: int
    n_tasks: int
    partition: dict[int, list[str]] | None  # static modes: task IDs per worker, fixed in advance
    peak_concurrency: int = 0
    workers_used: int = 0
    busy_ms: dict[int, float] = field(default_factory=dict)  # per worker: sum of task wall times


def contiguous_blocks(items: Sequence, n_parts: int) -> list[list]:
    """Split into at most n_parts contiguous, near-equal, non-empty blocks (earlier blocks larger)."""
    n_parts = min(n_parts, len(items))
    if n_parts == 0:
        return []
    base, extra = divmod(len(items), n_parts)
    out, i = [], 0
    for k in range(n_parts):
        step = base + (k < extra)
        out.append(list(items[i:i + step]))
        i += step
    return out


def partition(tasks: Sequence[Task], workers: int, mode: str, seed: int) -> list[list[Task]]:
    """The fixed per-worker task lists of a static mode (each worker runs its list in order)."""
    if mode == "static":
        order = list(tasks)
        random.Random(seed).shuffle(order)
        blocks = contiguous_blocks(order, workers)
        return blocks + [[] for _ in range(workers - len(blocks))]  # more workers than tasks
    if mode == "static_lpt":
        out = [[] for _ in range(workers)]
        heap = [(0.0, w) for w in range(workers)]  # (load, worker): ties go to the lower worker
        for task in sorted(tasks, key=lambda t: -t.cost):  # stable: equal costs keep list order
            load, w = heapq.heappop(heap)
            out[w].append(task)
            heapq.heappush(heap, (load + task.cost, w))
        return out
    raise ValueError(f"{mode!r} is not a static mode")


def run_tasks(
    tasks: Sequence[Task],
    fn: Callable[[Task], Any],
    *,
    mode: str,
    workers: int,
    seed: int = 0,
    catch: tuple[type[BaseException], ...] = (),
    on_done: Callable[[TaskOutcome], None] | None = None,
    stop: threading.Event | None = None,
) -> tuple[list[TaskOutcome], ScheduleInfo]:
    """Run fn(task) for every task; outcomes come back in task order, with scheduling facts.

    `on_done` is called after each task, possibly from several worker threads at once.
    """
    if mode not in MODES:
        raise ValueError(f"unknown scheduler mode {mode!r}")
    if workers < 1 or (mode == "serial" and workers != 1):
        raise ValueError(f"mode {mode} cannot use {workers} workers")
    if len({t.task_id for t in tasks}) != len(tasks):
        raise ValueError("duplicate task IDs")

    lock = threading.Lock()
    stop = stop if stop is not None else threading.Event()
    active = 0
    outcomes: dict[str, TaskOutcome] = {}
    info = ScheduleInfo(mode, workers, len(tasks), None)

    def execute(task: Task, worker_id: int) -> None:
        nonlocal active
        with lock:
            active += 1
            info.peak_concurrency = max(info.peak_concurrency, active)
        started, t0 = time.time(), time.perf_counter()
        result, error = None, None
        try:
            result = fn(task)
        except catch as exc:
            error = exc
        finally:
            wall = (time.perf_counter() - t0) * 1000
            with lock:
                active -= 1
                info.busy_ms[worker_id] = info.busy_ms.get(worker_id, 0.0) + wall
        out = TaskOutcome(task.task_id, worker_id, result, error, started, time.time(), wall)
        with lock:
            outcomes[task.task_id] = out
        if on_done is not None:  # outside the lock: slow logging must not hold up other workers
            on_done(out)

    if mode == "serial":
        try:
            for task in tasks:
                execute(task, 0)
        except BaseException:
            stop.set()
            raise
    else:
        if mode == "dynamic":
            shared: queue.Queue[Task] = queue.Queue()
            for task in tasks:
                shared.put(task)

            def next_task(_worker_id: int, _pos: int) -> Task | None:
                try:
                    return shared.get_nowait()
                except queue.Empty:
                    return None
        else:
            lists = partition(tasks, workers, mode, seed)
            info.partition = {w: [t.task_id for t in lst] for w, lst in enumerate(lists)}

            def next_task(worker_id: int, pos: int) -> Task | None:
                lst = lists[worker_id]
                return lst[pos] if pos < len(lst) else None

        def worker_loop(worker_id: int) -> None:
            pos = 0
            while not stop.is_set():
                task = next_task(worker_id, pos)
                if task is None:
                    return
                pos += 1
                try:
                    execute(task, worker_id)
                except BaseException:
                    stop.set()  # let the other workers finish their current task, then stop
                    raise

        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="itr-worker")
        try:
            futures = [pool.submit(worker_loop, w) for w in range(workers)]
            for f in futures:
                f.result()  # re-raise the first unexpected error
        except BaseException:  # includes Ctrl-C in the controller: no worker takes a new task
            stop.set()
            raise
        finally:
            pool.shutdown(wait=True)  # running tasks end within their per-cluster timeouts

    info.workers_used = len(info.busy_ms)
    return [outcomes[t.task_id] for t in tasks], info
