"""Attempts logging must not be silently skippable.

CLAUDE.md: "Log every attempt to the attempts table, including failures. It is
the debugging record now and the training set later."

Only refine.py ever did. Every ad-hoc harness used workspace.score directly, so
an entire day of experiments -- roughly 250 generations -- wrote zero rows and
is unrecoverable. These tests pin the fix.
"""
import sqlite3

import pytest

from solver import workspace

SCHEMA = """
CREATE TABLE functions (addr INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    func_addr INTEGER, iteration INTEGER, source_code TEXT,
    prompt_context TEXT, compiled INTEGER, compiler_stderr TEXT,
    score REAL, diff_summary TEXT, strategy TEXT, model TEXT,
    sampling TEXT, wall_ms INTEGER, token_cost INTEGER, created_at INTEGER);
"""


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA)
    c.execute("INSERT INTO functions (addr, name) VALUES (?,?)",
              (0x80001234, "someFunc"))
    c.commit()
    return c


def _att(compiled=True, score=42.5, exact=False):
    return workspace.Attempt(compiled, score, exact, "diff text",
                             "stderr text", "raw")


def test_successful_attempt_is_logged(conn):
    assert workspace.log_attempt(conn, "someFunc", "int x;", _att()) is True
    row = conn.execute("select score, compiled, strategy from attempts").fetchone()
    assert row[0] == 42.5 and row[1] == 1


def test_FAILURES_are_logged_too(conn):
    """The non-compiling rows are what made the extraction bugs findable."""
    workspace.log_attempt(conn, "someFunc", "garbage",
                          _att(compiled=False, score=0.0))
    row = conn.execute(
        "select compiled, compiler_stderr from attempts").fetchone()
    assert row[0] == 0
    assert row[1] == "stderr text", "stderr must survive; it is the diagnosis"


def test_unknown_function_does_not_raise(conn):
    """A harness must never crash because a name is missing -- it should just
    report that nothing was written."""
    assert workspace.log_attempt(conn, "noSuchFunc", "x", _att()) is False


def test_no_conn_is_a_no_op_not_an_error(conn):
    assert workspace.log_attempt(None, "someFunc", "x", _att()) is False


def test_sampling_metadata_round_trips(conn):
    workspace.log_attempt(conn, "someFunc", "x", _att(),
                          temperature=0.7, run_id="abc123",
                          extra={"num_predict": 6000})
    import json
    s = json.loads(conn.execute("select sampling from attempts").fetchone()[0])
    assert s["temperature"] == 0.7, "recorded temperature must be the real one"
    assert s["run_id"] == "abc123"
    assert s["num_predict"] == 6000


def test_score_accepts_conn_and_func_kwargs():
    """The signature must allow logging, or harnesses cannot comply."""
    import inspect
    sig = inspect.signature(workspace.score)
    assert "conn" in sig.parameters
    assert "func" in sig.parameters


def test_raw_response_is_captured_when_the_column_exists(conn):
    """Only post-extraction source was ever stored, so a failed extraction was
    unreadable afterwards -- which is how 13.8% of compile failures hid."""
    conn.execute("ALTER TABLE attempts ADD COLUMN raw_response TEXT")
    conn.execute("ALTER TABLE attempts ADD COLUMN extract_status TEXT")
    conn.commit()
    workspace.log_attempt(conn, "someFunc", "", _att(compiled=False, score=0.0),
                          raw_response="```c\nint x;  (truncated",
                          extract_status="unterminated")
    row = conn.execute(
        "select raw_response, extract_status from attempts").fetchone()
    assert "truncated" in row[0], "raw model output must survive"
    assert row[1] == "unterminated"


def test_logging_still_works_without_the_new_columns(conn):
    """Older databases lack them; logging must degrade, never raise."""
    assert workspace.log_attempt(conn, "someFunc", "x", _att(),
                                 raw_response="ignored me") is True
