"""Grounded tests: assertions against the real KB and repo, not fixtures.

Three bugs in one day passed their unit tests and were wrong anyway, all at the
mock/reality boundary:

  heldout_names     read entry["name"]; the split files key on "function", so
                    the filter returned the empty set and silently disabled the
                    one guard that must never be wrong.
  _global_evidence  formatted `global:{addr:#010x}`; the evidence tier spells
                    it `global:0x801107D8`, so 1,033 of 1,166 addresses
                    returned zero citations without erroring.
  absent_body       no fixture covered an m2c draft that is only a comment, so
                    the enumerator ranked those functions as the easiest work
                    available.

A fixture built in the shape the code expects cannot catch a code/data
disagreement -- it encodes the same assumption twice and calls the agreement a
pass. So anything that parses, joins, or filters PRODUCTION data gets a test
that reads production data.

Skipped, not failed, where the WSL build tree is absent: these run under WSL
against ~/decomp, and the suite must stay green on Windows.
"""

import os
import sqlite3
from pathlib import Path

import pytest

REPO = os.path.expanduser("~/decomp/sbk1")
DB = os.path.expanduser("~/decomp/kb-sbk1.sqlite")

# Gitignored, so absent from a public clone: inputs derived from the game binary,
# run data, and tool caches. See .gitignore.
ROOT = Path(__file__).resolve().parent.parent
LOCAL_ONLY = ("tests/fixtures", "eval/results", ".cache")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "grounded: reads the real KB/repo; skipped when absent")


@pytest.fixture(scope="session")
def repo_path():
    if not os.path.isdir(REPO):
        pytest.skip(f"target repo not present at {REPO}")
    from pathlib import Path
    return Path(REPO)


@pytest.fixture(scope="session")
def kb():
    if not os.path.isfile(DB):
        pytest.skip(f"knowledge base not present at {DB}")
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA busy_timeout = 60000")
    yield conn
    conn.close()


# A test that reads a local-only input is skipped, naming the file, whether it reads it
# at import or at run time. A missing file anywhere else still fails: the skip is for
# inputs a clone cannot have, not for paths the code got wrong.
def _skip_if_local_only(error):
    if not isinstance(error.filename, (str, os.PathLike)):
        return
    path = Path(os.path.realpath(error.filename))
    for local in LOCAL_ONLY:
        if path.is_relative_to(ROOT / local):
            pytest.skip(f"local-only input absent: {path.relative_to(ROOT).as_posix()}",
                        allow_module_level=True)


class _Module(pytest.Module):
    def _getobj(self):
        try:
            return super()._getobj()
        except FileNotFoundError as error:
            _skip_if_local_only(error)
            raise


def pytest_pycollect_makemodule(module_path, parent):
    return _Module.from_parent(parent, path=module_path)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item):
    try:
        return (yield)
    except FileNotFoundError as error:
        _skip_if_local_only(error)
        raise


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    try:
        return (yield)
    except FileNotFoundError as error:
        _skip_if_local_only(error)
        raise
