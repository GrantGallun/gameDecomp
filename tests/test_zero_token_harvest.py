"""The harvest must FIRE on the residual it was written for.

CLAUDE.md's fifth rule: a pass that returns nothing looks exactly like a pass
with nothing to do. Declining is the easy half to test, so these assert the
selector actually produces the scratch-ID-shaped names first.
"""

import json
import pathlib
import sqlite3

import pytest

from eval import zero_token_harvest as zth


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.execute("create table functions (addr integer primary key, name text)")
    db.execute("create table attempts (id integer primary key,"
               " func_addr integer, exact integer, score real)")
    return db


def _add(db, addr, name):
    db.execute("insert into functions values (?,?)", (addr, name))


def test_fires_on_the_scratch_shaped_names(conn, tmp_path):
    """The motivating residual: 5-10 char alphanumeric names, never attempted."""
    for addr, name in enumerate(
            ["strlen", "memcpy", "alHeapInit", "Fdrumsoff", "fixedSine"], 1):
        _add(conn, addr, name)
    found = zth.candidates(conn, tmp_path, unblocked_only=True, explicit=[])
    assert found == ["Fdrumsoff", "alHeapInit", "fixedSine", "memcpy", "strlen"]


def test_shape_predicate_matches_the_tool_heuristic():
    assert zth.scratch_shaped("strlen")
    assert zth.scratch_shaped("alHeapInit")
    assert not zth.scratch_shaped("osGetTime_x")   # underscore
    assert not zth.scratch_shaped("main")          # under 5
    assert not zth.scratch_shaped("initRaceCameraChase")  # over 10


def test_heldout_is_subtracted(conn, tmp_path):
    _add(conn, 1, "strlen")
    _add(conn, 2, "memcpy")
    (tmp_path / "sbk1_v3.json").write_text(
        json.dumps({"heldout": [{"function": "strlen"}]}), encoding="utf-8")
    found = zth.candidates(conn, tmp_path, unblocked_only=True, explicit=[])
    assert found == ["memcpy"]
    assert "strlen" in zth.heldout_names(tmp_path)


def test_heldout_matches_the_real_split_files():
    """Against the files on disk, not a fixture in the shape I assumed.

    The first parser read entry["name"]; the splits key on "function", so it
    returned an empty set and the guard was inert while its mock-based test
    stayed green. The heldout filter is the one that must never silently
    decline, so it is checked against production data.
    """
    names = zth.heldout_names(pathlib.Path("eval/sets"))
    assert len(names) >= 49
    assert "getAssetTableImageAndExplicitPalette" in names


def test_both_key_spellings_are_accepted(tmp_path):
    (tmp_path / "sbk1_va.json").write_text(
        json.dumps({"heldout": [{"function": "a"}, {"name": "b"}, "c"]}),
        encoding="utf-8")
    assert zth.heldout_names(tmp_path) == {"a", "b", "c"}


def test_already_attempted_is_subtracted(conn, tmp_path):
    _add(conn, 1, "strlen")
    _add(conn, 2, "memcpy")
    conn.execute("insert into attempts (func_addr, exact, score)"
                 " values (1, 0, 42.0)")
    found = zth.candidates(conn, tmp_path, unblocked_only=True, explicit=[])
    assert found == ["memcpy"]


def test_explicit_names_bypass_the_shape_filter(conn, tmp_path):
    _add(conn, 1, "initRaceCameraChase")
    found = zth.candidates(conn, tmp_path, unblocked_only=True,
                           explicit=["initRaceCameraChase"])
    assert found == ["initRaceCameraChase"]


class _FakeConn:
    """Minimal stand-in: repair_chain only needs structgen.layout's query."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, _sql, _params=()):
        rows = list(self._rows)

        class _Cursor(list):
            def fetchall(self):
                return list(self)

        return _Cursor(rows)


DO_WHILE_DRAFT = '''#include "common.h"

void f(T *a) {
    s32 i = 0;
    do {
        a->count = i;
        i += 1;
    } while (i < 4);
}
'''


def test_repair_chain_keeps_do_and_still_fires_typedecl():
    """The chain no longer lowers the loop, and the other stages still compose.

    CHANGED 2026-09-17 with the removal of the `do`-token refusal. The old assertion here was
    `applied == ["do-while", "typedecl"]` with `"for (;;)" in code` -- it pinned a transformation that
    is NOT codegen-neutral: on drawRaceSplitscreenSelectOption2Frame the lowering alone turns a
    BYTE-EXACT body into 99.395. It now pins the opposite, which is the property that matters: the loop
    the author (or m2c) wrote reaches IDO unchanged.
    """
    conn = _FakeConn([("param0", 8, 4, 1, 1)])
    code, applied, plans, declined = zth.repair_chain(
        conn, "f", DO_WHILE_DRAFT, set())
    assert "do-while" not in applied
    assert applied == ["typedecl"]
    assert "do {" in code, "the chain must not lower a loop the compiler accepts"
    assert "for (;;)" not in code
    assert plans and "} T;" in plans[0]["text"]
    assert declined == ""


def test_repair_chain_declines_cleanly_with_nothing_to_do():
    conn = _FakeConn([])
    code, applied, plans, declined = zth.repair_chain(
        conn, "f", 'void f(void) {\n    return;\n}\n', set())
    assert applied == [] and plans == [] and declined == ""
