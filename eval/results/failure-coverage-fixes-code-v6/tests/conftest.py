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

import pytest

REPO = os.path.expanduser("~/decomp/sbk1")
DB = os.path.expanduser("~/decomp/kb-sbk1.sqlite")


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
