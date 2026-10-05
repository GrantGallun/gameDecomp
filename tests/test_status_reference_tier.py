"""The reference-type-assisted tier: it must FIRE on a real reference-only type, and must not on a
type any header makes public.

MOTIVATING RESIDUAL (`eval/results/intake-20260921/CONTAMINATION.md`): `solver/workspace.m2c_draft`
prefers the target repo's own `nonmatchings/<fn>/base.c`, which is produced with the real source as
m2c context. Winning sources like `lockRelocatableHeapBlock` use `RelocatableHeapBlock`, a type only
the target's `src/*.c` defines. On the real matched set this tier is 25 of 347 byte-exact.

THE BUG THIS PINS: the first version recognised two definition forms, not three, so a header's
`typedef struct X X;` did not subtract `X` and header-known types were reported as reference-only --
77 where the careful count is 25.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval import status                                                       # noqa: E402


def _tree(tmp_path: Path) -> Path:
    tree = tmp_path / "sbk1"
    (tree / "src").mkdir(parents=True)
    (tree / "include" / "game").mkdir(parents=True)
    (tree / "src" / "heap.c").write_text(
        "typedef struct { s32 size; } RelocatableHeapBlock;\n"
        "struct ForwardOnly { s32 x; };\n"
        "typedef struct { u8 a; } PublicShape;\n", encoding="utf-8")
    (tree / "include" / "game" / "heap.h").write_text(
        "typedef struct ForwardOnly ForwardOnly;\n"        # made public by a forward typedef
        "typedef struct { u8 a; } PublicShape;\n", encoding="utf-8")
    return tree


def test_only_a_type_no_header_makes_public_is_reference_only(tmp_path):
    types = status.reference_only_types(_tree(tmp_path))
    assert "RelocatableHeapBlock" in types, "defined only in src/"
    assert "PublicShape" not in types, "a header defines it too"
    # THE BUG: a header's forward `typedef struct X X;` must count as making X public.
    assert "ForwardOnly" not in types, types


def test_an_unavailable_build_tree_reads_zero_rather_than_failing(tmp_path):
    assert status.reference_only_types(None) == set()
    assert status.reference_only_types(tmp_path / "missing") == set()


def _db(tmp_path: Path, rows) -> Path:
    db = tmp_path / "kb.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
        create table functions (addr integer, name text);
        create table attempts (id integer primary key, func_addr integer, strategy text,
                               source_code text, exact integer);
        create table evidence (id integer); create table inference (id integer);""")
    for i, (name, strategy, source) in enumerate(rows):
        conn.execute("insert into functions values (?, ?)", (i + 1, name))
        conn.execute("insert into attempts (func_addr, strategy, source_code, exact) values (?,?,?,1)",
                     (i + 1, strategy, source))
    conn.commit()
    conn.close()
    return db


def test_the_tier_fires_on_a_winning_source_that_uses_one(tmp_path, monkeypatch):
    tree = _tree(tmp_path)
    db = _db(tmp_path, [
        ("lockRelocatableHeapBlock", "exact-reverify",
         "void lockRelocatableHeapBlock(RelocatableHeapBlock *b) { b->size = 0; }"),
        ("cleanFunction", "diffrepair", "void cleanFunction(PublicShape *p) { p->a = 0; }"),
    ])
    monkeypatch.setattr(status.matched_mod, "matched_in_db",
                        lambda conn: {"lockRelocatableHeapBlock", "cleanFunction"})
    monkeypatch.setattr(status.attempt_receipts, "unknown_exact_count", lambda conn: 0)
    monkeypatch.chdir(tmp_path)
    c = status.counts(db, tree)
    assert c["reference_type_assisted"] == 1
    assert c["solved"] == 1, "the clean function is still SOLVED"
    assert c["exact"] == 2, "no match is lost -- only the tier it is credited to changes"


def test_one_clean_exact_source_keeps_a_function_solved(tmp_path, monkeypatch):
    """CONSERVATIVE BY CONSTRUCTION: if any exact source avoids the reference type, the function was
    reachable without it, so it is not reclassified."""
    tree = _tree(tmp_path)
    db = _db(tmp_path, [
        ("f", "exact-reverify", "void f(RelocatableHeapBlock *b) { b->size = 0; }"),
    ])
    conn = sqlite3.connect(db)
    conn.execute("insert into attempts (func_addr, strategy, source_code, exact) values (1,?,?,1)",
                 ("diffrepair", "void f(void *b) { *(s32 *)b = 0; }"))
    conn.commit()
    conn.close()
    monkeypatch.setattr(status.matched_mod, "matched_in_db", lambda conn: {"f"})
    monkeypatch.setattr(status.attempt_receipts, "unknown_exact_count", lambda conn: 0)
    monkeypatch.chdir(tmp_path)
    c = status.counts(db, tree)
    assert c["reference_type_assisted"] == 0
    assert c["solved"] == 1
