"""The near-miss closer's worker pool.

The failure this guards against is the one CLAUDE.md catalogues: a pass that runs
its serial path, reports plausible output, and never reaches the code that was
added. So the tests below assert the pool is *entered* with the requested worker
count and that `jobs` functions are genuinely in flight at once -- a submit/collect
loop that awaited each future would still produce a complete `state.json`, and a
completeness assertion alone would pass on it.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

from eval import close_nearmiss as cn


def _kb(path):
    """The two tables `best_compiled_source` reads, with no rows."""
    with sqlite3.connect(path) as conn:
        conn.execute("create table functions (addr integer, name text)")
        conn.execute("create table attempts (id integer, func_addr integer, "
                     "source_code text, score real, compiled integer, exact integer)")
    return path


class _InProcessPool:
    """Stands in for ProcessPoolExecutor so the test can observe dispatch.

    Spawned children re-import the module, so a monkeypatched `close_one` would not
    reach them. Running the submitted callables on threads in this process keeps the
    orchestration under test while letting the test see the worker count and the
    real concurrency.
    """

    entered = []

    def __init__(self, max_workers=None, mp_context=None, initializer=None, initargs=()):
        self.entered.append(max_workers)
        if initializer:
            initializer(*initargs)
        self._threads = ThreadPoolExecutor(max_workers=max_workers)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._threads.shutdown()
        return False

    def submit(self, fn, *args):
        return self._threads.submit(fn, *args)


def test_jobs_above_one_enters_the_pool_and_runs_functions_concurrently(monkeypatch, tmp_path):
    jobs = 3
    names = [f"func_{i}" for i in range(jobs)]
    barrier = threading.Barrier(jobs, timeout=20)

    def fake_close_one(conn, repo, name, **opts):
        # Releases only when `jobs` functions are inside at the same time. A parent
        # that submitted one job and waited for it would break the barrier instead,
        # which surfaces as a `worker-raised` row.
        barrier.wait()
        return {"function": name, "status": "ok", "exact": False}

    _InProcessPool.entered = []
    monkeypatch.setattr(cn, "ProcessPoolExecutor", _InProcessPool)
    monkeypatch.setattr(cn, "close_one", fake_close_one)

    db = _kb(tmp_path / "kb.sqlite")
    out = tmp_path / "out"
    out.mkdir()
    state_path = out / "state.json"
    conn = sqlite3.connect(db)
    state = cn.close_many(conn, tmp_path, names, {}, db=str(db), jobs=jobs, seconds=0.0,
                          state={}, state_path=state_path)

    assert [r["status"] for r in state.values()] == ["ok"] * jobs
    assert json.loads(state_path.read_text()).keys() == set(names)
    assert _InProcessPool.entered == [jobs], "the pool must be entered with the requested jobs"


def test_jobs_of_one_keeps_the_serial_path(monkeypatch, tmp_path):
    def explode(*a, **kw):
        raise AssertionError("jobs=1 must not construct a pool")

    seen = []

    def fake_close_one(conn, repo, name, **opts):
        seen.append(name)
        return {"function": name, "status": "ok"}

    monkeypatch.setattr(cn, "ProcessPoolExecutor", explode)
    monkeypatch.setattr(cn, "close_one", fake_close_one)

    db = _kb(tmp_path / "kb.sqlite")
    state = cn.close_many(sqlite3.connect(db), tmp_path, ["a", "b", "c"], {}, db=str(db),
                          jobs=1, seconds=0.0, state={}, state_path=tmp_path / "state.json")
    assert seen == ["a", "b", "c"]
    assert set(state) == {"a", "b", "c"}


def test_solved_functions_are_not_resubmitted(monkeypatch, tmp_path):
    submitted = []

    class Pool(_InProcessPool):
        def submit(self, fn, *args):
            submitted.append(args[0])
            return super().submit(fn, *args)

    monkeypatch.setattr(cn, "ProcessPoolExecutor", Pool)
    monkeypatch.setattr(cn, "close_one", lambda conn, repo, name, **o: {"function": name})

    db = _kb(tmp_path / "kb.sqlite")
    cn.close_many(sqlite3.connect(db), tmp_path, ["done", "todo"], {}, db=str(db), jobs=2,
                  seconds=0.0, state={"done": {"function": "done"}},
                  state_path=tmp_path / "state.json")
    assert submitted == ["todo"]


def test_spawn_workers_open_their_own_connection(tmp_path):
    """End-to-end through the real spawn pool: the path a serial test never reaches."""
    db = _kb(tmp_path / "kb.sqlite")
    out = tmp_path / "out"
    rc = cn.main(["--db", str(db), "--repo", str(tmp_path), "--out", str(out),
                  "--functions", "alpha,beta,gamma", "--jobs", "2"])
    assert rc == 0
    state = json.loads((out / "state.json").read_text())
    assert set(state) == {"alpha", "beta", "gamma"}
    # No compiling attempt exists, so each worker reports it without touching a repo.
    assert {r["status"] for r in state.values()} == {"no-compiling-candidate"}
    assert json.loads((out / "summary.json").read_text())["population"] == 3


def test_a_raising_worker_does_not_lose_the_function(monkeypatch, tmp_path):
    def fake_close_one(conn, repo, name, **opts):
        if name == "bad":
            raise RuntimeError("worker died")
        return {"function": name, "status": "ok"}

    monkeypatch.setattr(cn, "ProcessPoolExecutor", _InProcessPool)
    monkeypatch.setattr(cn, "close_one", fake_close_one)

    db = _kb(tmp_path / "kb.sqlite")
    state = cn.close_many(sqlite3.connect(db), tmp_path, ["bad", "good"], {}, db=str(db),
                          jobs=2, seconds=0.0, state={}, state_path=tmp_path / "state.json")
    assert state["bad"]["status"] == "worker-raised"
    assert "worker died" in state["bad"]["error"]
    assert state["good"]["status"] == "ok"
