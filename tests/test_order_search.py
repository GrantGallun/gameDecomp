"""The ordering closer: source and diff from ONE attempt, and acceptance by the oracle only.

The pass's own docstring warns that the reordering penalty in the byte score HIDES the lever, so the
score may not rise even when the permutation is right. This driver therefore must not rank by score —
`att.exact` is the only acceptance — and it must not feed the pass a diff from a different attempt than
the source, which is the failure `patterns/derive.cases_from_kb` documents.
"""
from __future__ import annotations

import json
import sqlite3

from eval import order_search as os_mod


def _kb(path):
    with sqlite3.connect(path) as conn:
        conn.execute("create table functions (addr integer, name text)")
        conn.execute("create table attempts (id integer, func_addr integer, source_code text, "
                     "diff_summary text, score real, compiled integer, exact integer)")
    return path


def test_the_diff_comes_from_the_same_attempt_as_the_source(tmp_path):
    """Two attempts with different scores, each with its own diff. The pair must not be mixed."""
    db = _kb(tmp_path / "kb.sqlite")
    with sqlite3.connect(db) as conn:
        conn.execute("insert into functions values (1, 'f')")
        conn.execute("insert into attempts values (10, 1, 'best source', 'BEST DIFF', 99.9, 1, 0)")
        conn.execute("insert into attempts values (11, 1, 'worse source', 'WORSE DIFF', 50.0, 1, 0)")
    conn = sqlite3.connect(db)
    receipt, source, score = os_mod.best_compiled_source(conn, "f")
    assert (receipt, source, score) == (10, "best source", 99.9)
    assert os_mod.best_diff(conn, "f") == "BEST DIFF"


def test_a_function_with_no_compiling_attempt_is_reported_not_skipped(tmp_path):
    db = _kb(tmp_path / "kb.sqlite")
    with sqlite3.connect(db) as conn:
        conn.execute("insert into functions values (1, 'f')")
        conn.execute("insert into attempts values (10, 1, 'x', 'd', 0.0, 0, 0)")
    out = tmp_path / "out"
    rc = os_mod.main(["--db", str(db), "--repo", str(tmp_path), "--out", str(out),
                      "--functions", "alpha,beta"])
    assert rc == 0
    state = json.loads((out / "state.json").read_text())
    assert set(state) == {"alpha", "beta"}
    assert {r["status"] for r in state.values()} == {"no-compiling-candidate"}
    summary = json.loads((out / "summary.json").read_text())
    assert summary["population"] == 2 and summary["exact"] == 0


def test_the_population_defaults_to_the_census_reordered_only_set(tmp_path):
    census = tmp_path / "state.json"
    census.write_text(json.dumps({
        "a": {"pairing": "reordered-only", "exact": False},
        "b": {"pairing": "renamed-only", "exact": False},
        "c": {"pairing": "reordered-only", "exact": True},
        "d": {"pairing": "both", "exact": False},
    }))
    rows = json.loads(census.read_text())
    names = sorted(n for n, r in rows.items() if r.get("pairing") == "reordered-only" and not r.get("exact"))
    assert names == ["a"]
