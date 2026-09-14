"""Legacy exactness is reverified, never reconstructed from similarity."""

import sqlite3

from eval.reverify_exact import legacy_candidates


def _db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE functions (addr INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE attempts (
            id INTEGER PRIMARY KEY, func_addr INTEGER, compiled INTEGER,
            score REAL, exact INTEGER, source_code TEXT);
        INSERT INTO functions VALUES (1, 'legacy'), (2, 'known');
        INSERT INTO attempts VALUES (10, 1, 1, 100.0, NULL, 'candidate a');
        INSERT INTO attempts VALUES (11, 1, 1, 99.999, NULL, 'candidate b');
        INSERT INTO attempts VALUES (12, 1, 1, 90.0, NULL, 'too low');
        INSERT INTO attempts VALUES (20, 2, 1, 100.0, 1, 'verified');
        INSERT INTO attempts VALUES (21, 2, 1, 100.0, NULL, 'old duplicate');
    """)
    return conn


def test_selects_unknown_high_scores_but_does_not_call_them_exact():
    got = legacy_candidates(_db(), floor=99.99)
    assert list(got) == ["legacy"]
    assert [(aid, score) for aid, score, _src in got["legacy"]] == [
        (10, 100.0), (11, 99.999)]


def test_respects_per_function_compile_budget():
    got = legacy_candidates(_db(), floor=0, per_function=1)
    assert len(got["legacy"]) == 1
