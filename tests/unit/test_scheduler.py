"""Unit tests for the scheduler with a fake task function (gates G5.2, G5.3, G5.5, G5.6)."""

import threading
import time

import pytest

from dnarecon.scheduler import MODES, Task, partition, run_tasks


def tasks(n, sleep=0.02):
    return [Task(f"t{i}", [i], cost=float(i)) for i in range(n)]


def sleepy(task):
    time.sleep(0.02)
    return sum(task.items)


@pytest.mark.parametrize("mode,workers", [("serial", 1), ("dynamic", 1), ("dynamic", 3),
                                          ("static", 3), ("static_lpt", 3)])
def test_g5_2_every_task_runs_exactly_once_results_in_task_order(mode, workers):
    seen, lock = [], threading.Lock()

    def fn(task):
        with lock:
            seen.append(task.task_id)
        return sleepy(task)

    outcomes, info = run_tasks(tasks(10), fn, mode=mode, workers=workers, seed=1)
    assert sorted(seen) == sorted(f"t{i}" for i in range(10)) and len(seen) == 10
    assert [o.task_id for o in outcomes] == [f"t{i}" for i in range(10)]
    assert [o.result for o in outcomes] == list(range(10))
    assert info.n_tasks == 10


@pytest.mark.parametrize("mode", ["dynamic", "static", "static_lpt"])
def test_g5_3_and_g5_6_configured_concurrency_reached_never_exceeded(mode):
    outcomes, info = run_tasks(tasks(12), sleepy, mode=mode, workers=4, seed=1)
    assert info.peak_concurrency == 4 and info.workers_used == 4
    assert {o.worker_id for o in outcomes} == {0, 1, 2, 3}


def test_serial_runs_on_one_worker_one_at_a_time():
    outcomes, info = run_tasks(tasks(5), sleepy, mode="serial", workers=1)
    assert info.peak_concurrency == 1 and {o.worker_id for o in outcomes} == {0}
    assert info.partition is None


def test_more_workers_than_tasks():
    outcomes, info = run_tasks(tasks(2), sleepy, mode="dynamic", workers=4)
    assert len(outcomes) == 2 and info.workers_used == 2


def test_static_partition_is_fixed_by_the_seed_and_balanced_by_count():
    a = partition(tasks(10), 3, "static", seed=7)
    b = partition(tasks(10), 3, "static", seed=7)
    assert [[t.task_id for t in w] for w in a] == [[t.task_id for t in w] for w in b]
    assert [len(w) for w in a] == [4, 3, 3]
    assert sorted(t.task_id for w in a for t in w) == sorted(t.task_id for t in tasks(10))
    c = partition(tasks(10), 3, "static", seed=8)
    assert [[t.task_id for t in w] for w in a] != [[t.task_id for t in w] for w in c]


def test_static_run_follows_the_recorded_partition():
    outcomes, info = run_tasks(tasks(9), sleepy, mode="static", workers=3, seed=3)
    by_task = {o.task_id: o.worker_id for o in outcomes}
    assert all(by_task[t] == w for w, ids in info.partition.items() for t in ids)


def test_static_lpt_assigns_longest_first_to_least_loaded():
    lists = partition([Task("a", [], 5), Task("b", [], 4), Task("c", [], 3), Task("d", [], 3)], 2,
                      "static_lpt", seed=0)
    assert [[t.task_id for t in w] for w in lists] == [["a", "d"], ["b", "c"]]  # loads 8 / 7


def test_g5_5_caught_error_is_recorded_and_others_continue():
    def fn(task):
        if task.task_id == "t3":
            raise KeyError("bad cluster")
        return sleepy(task)

    outcomes, _ = run_tasks(tasks(8), fn, mode="dynamic", workers=2, catch=(KeyError,))
    bad = [o for o in outcomes if o.error is not None]
    assert [o.task_id for o in bad] == ["t3"] and bad[0].result is None
    assert all(o.result == int(o.task_id[1:]) for o in outcomes if o.error is None)


def test_unexpected_error_stops_new_tasks_and_is_raised():
    started = []

    def fn(task):
        started.append(task.task_id)
        if task.task_id == "t0":
            raise RuntimeError("bug")
        return sleepy(task)

    with pytest.raises(RuntimeError, match="bug"):
        run_tasks(tasks(50), fn, mode="dynamic", workers=2)
    assert len(started) < 50


@pytest.mark.parametrize("mode,workers", [("serial", 2), ("dynamic", 0), ("fifo", 2)])
def test_bad_settings_rejected(mode, workers):
    with pytest.raises(ValueError):
        run_tasks(tasks(2), sleepy, mode=mode, workers=workers)


def test_duplicate_task_ids_rejected():
    with pytest.raises(ValueError):
        run_tasks([Task("x", []), Task("x", [])], sleepy, mode="serial", workers=1)


def test_on_done_called_once_per_task():
    done = []
    run_tasks(tasks(6), sleepy, mode="dynamic", workers=3, on_done=done.append)
    assert sorted(o.task_id for o in done) == sorted(f"t{i}" for i in range(6))
    assert set(MODES) == {"serial", "static", "static_lpt", "dynamic"}
