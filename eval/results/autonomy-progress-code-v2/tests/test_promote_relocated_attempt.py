import sqlite3

import pytest

from tools import promote_relocated_attempt


def attempt_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE functions(addr INTEGER PRIMARY KEY, name TEXT);"
        "CREATE TABLE attempts(id INTEGER PRIMARY KEY, func_addr INTEGER, "
        "source_code TEXT);"
        "INSERT INTO functions VALUES(4096, 'wanted');"
        "INSERT INTO attempts VALUES(7, 4096, 'void wanted(void) {}');"
    )
    return conn


def test_source_from_attempt_is_bound_to_function():
    conn = attempt_db()
    assert promote_relocated_attempt.source_from_attempt(
        conn, 7, "wanted") == "void wanted(void) {}"


def test_source_from_attempt_rejects_wrong_function():
    conn = attempt_db()
    with pytest.raises(ValueError, match="belongs to wanted"):
        promote_relocated_attempt.source_from_attempt(conn, 7, "other")
