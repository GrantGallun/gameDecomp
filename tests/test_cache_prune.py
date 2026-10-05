"""The fast_runtime cache pruner evicts least-recently-used entries and never touches a locked one."""
import os

import pytest

from eval import cache_prune

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the cache and its locks are WSL-side (flock)")


def entry(root, kind, name, size, used):
    directory = root / kind / name[:2] / name
    directory.mkdir(parents=True)
    (directory / "value.json").write_bytes(b"x" * size)
    (directory / "lock").touch()
    os.utime(directory / "value.json", (used, used))
    return directory


def test_evicts_oldest_until_under_budget(tmp_path):
    old = entry(tmp_path, "compile", "aa01", 600, 1000)
    mid = entry(tmp_path, "compile", "bb02", 300, 2000)
    new = entry(tmp_path, "compile", "cc03", 300, 3000)
    report = cache_prune.prune(tmp_path, "compile", 700, apply=False)
    assert report["over_budget_entries"] == 1 and old.exists()          # dry run changes nothing
    report = cache_prune.prune(tmp_path, "compile", 700, apply=True)
    assert report["evicted"] == 1 and report["freed_bytes"] == 600
    assert not (old / "value.json").exists() and (mid / "value.json").exists() and (new / "value.json").exists()
    # The directory and lock survive, so a worker already waiting on this lock can write its miss.
    assert old.is_dir() and (old / "lock").exists()
    assert cache_prune.prune(tmp_path, "compile", 700, apply=True)["entries"] == 2


def test_locked_entry_is_skipped_not_waited_on(tmp_path):
    import fcntl
    held = entry(tmp_path, "compile", "aa01", 600, 1000)
    entry(tmp_path, "compile", "bb02", 600, 2000)
    with (held / "lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        report = cache_prune.prune(tmp_path, "compile", 700, apply=True)
    assert report["skipped_locked"] == 1 and report["evicted"] == 0 and held.exists()


def test_worker_waiting_on_an_evicted_entry_can_still_write_its_miss(tmp_path):
    import fcntl
    old = entry(tmp_path, "compile", "aa01", 600, 1000)
    entry(tmp_path, "compile", "bb02", 600, 2000)
    waiting = (old / "lock").open("a+b")             # a worker opened the lock before the prune ran
    try:
        assert cache_prune.prune(tmp_path, "compile", 700, apply=True)["evicted"] == 1
        fcntl.flock(waiting, fcntl.LOCK_EX)           # ...and gets it afterwards
        (old / "value.json.tmp").write_text("{}")
        (old / "value.json.tmp").replace(old / "value.json")
    finally:
        waiting.close()
    assert (old / "value.json").read_text() == "{}"


def test_missing_kind_is_an_empty_report(tmp_path):
    assert cache_prune.prune(tmp_path, "semantic", 1, apply=True)["entries"] == 0
