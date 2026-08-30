"""Sibling context may rank with the reference index but not copy future C."""

import sqlite3
from pathlib import Path

from eval.sibling_coverage import similarity_band
from solver import siblings


def _db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE functions (addr INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE attempts (
            id INTEGER PRIMARY KEY, func_addr INTEGER, compiled INTEGER,
            score REAL, exact INTEGER, source_code TEXT);
        INSERT INTO functions VALUES (1, 'good'), (2, 'scoreOnly');
        INSERT INTO attempts VALUES (10, 1, 1, 90.0, 1, 'recovered good C');
        INSERT INTO attempts VALUES (11, 2, 1, 100.0, 0, 'future-looking C');
    """)
    return conn


def test_verified_pool_requires_positive_exact_receipt():
    assert siblings.verified_sources(_db()) == {"good": "recovered good C"}


def test_context_uses_recovered_source_not_reference_path(monkeypatch, tmp_path):
    reference = tmp_path / "finished.c"
    reference.write_text("int good(void) { return 999; }")
    monkeypatch.setattr(
        siblings, "find",
        lambda *a, **k: [("good", 0.91, reference)])
    block = siblings.context_block(
        tmp_path, "target", sources={"good": "int good(void) { return 1; }"})
    assert "return 1" in block
    assert "return 999" not in block


def test_sibling_pool_digest_changes_with_source():
    a = siblings.source_digest({"f": "one"})
    b = siblings.source_digest({"f": "two"})
    assert a and a != b


def test_similarity_bands_do_not_overstate_weak_matches():
    assert similarity_band(0.44) == "<0.45"
    assert similarity_band(0.45) == "0.45-0.75"
    assert similarity_band(0.75) == "0.75-0.90"
    assert similarity_band(0.90) == "0.90+"
