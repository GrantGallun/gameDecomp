"""The ledger must FIRE on the two cases LEDGER.md gates it on.

A ledger that enumerates nothing looks exactly like a codebase with no
unknowns -- CLAUDE.md's fifth rule, and the failure this project keeps
rediscovering. Both drafts below are verbatim from the repo's own
nonmatchings/ workspaces.
"""

import json
import sqlite3

import pytest

from solver import unknowns


class _Conn:
    """Answers structgen.layout's and _global_evidence's queries only."""

    def __init__(self, layout_rows=(), global_rows=()):
        self._layout = list(layout_rows)
        self._globals = list(global_rows)

    def execute(self, sql, params=()):
        # BOTH queries say "FROM evidence"; routing on that fed the layout
        # query the globals rows and the gate test failed against a correct
        # module. structgen's query is the one scoped to a function.
        rows = self._layout if "func_addr" in sql else self._globals

        class _Cur(list):
            lastrowid = 1

            def fetchall(self):
                return list(self)

        return _Cur(rows)


FDRUMSOFF = '''#include "common.h"

s32 Fdrumsoff(PlayerCommandState *arg0, s32 arg1) {
    arg0->pdrums = NULL;
    return arg1;
}
'''

RELEASE = '''#include "common.h"

void releaseRelocatableHeapBlockMetadata(RelocatableHeapBlock *block) {
    u16 temp_t7;

    temp_t7 = gRelocatableHeapUsedBlockCount - 1;
    gRelocatableHeapUsedBlockCount = temp_t7;
    gRelocatableHeapFreeBlockStack[temp_t7 & 0xFFFF] = block;
    block->status = 0;
}
'''


def test_fires_on_fdrumsoff_undeclared_type():
    """LEDGER.md gate 1: one undeclared_type, accessed 112/4, resolver typedecl."""
    conn = _Conn(layout_rows=[("param0", 112, 4, 1, 1)])
    found = unknowns.enumerate_unknowns(conn, "Fdrumsoff", FDRUMSOFF,
                                        {"s32"}, {})
    types = [u for u in found if u.kind == "undeclared_type"]
    assert len(types) == 1
    u = types[0]
    assert u.subject == "Fdrumsoff:param0"
    assert u.status == "free"
    assert u.resolver == "solver.typedecl"
    assert u.capabilities["accessed"] == [{"offset": 112, "width": 4,
                                           "signed": None}]
    assert u.capabilities["members"] == ["pdrums"]


def test_field_names_are_recorded_as_unpinnable():
    conn = _Conn(layout_rows=[("param0", 112, 4, 1, 1)])
    found = unknowns.enumerate_unknowns(conn, "Fdrumsoff", FDRUMSOFF,
                                        {"s32"}, {})
    names = [u for u in found if u.kind == "field_name"]
    assert [u.subject for u in names] == ["Fdrumsoff:param0.pdrums"]
    assert all(u.status == "unpinnable" for u in names)
    assert all(u.weight == 0 for u in names)


def test_fires_on_the_undeclared_global_with_evidence_width():
    """LEDGER.md gate 2: width 2 from evidence, joined on address."""
    conn = _Conn(layout_rows=[("param0", 0, 4, 1, 0)],
                 global_rows=[(9001, 0, 2, 0, 1), (9002, 0, 2, 0, 0)])
    symbols = {"gRelocatableHeapUsedBlockCount": 0x80110918,
               "gRelocatableHeapFreeBlockStack": 0x801107D8}
    found = unknowns.enumerate_unknowns(
        conn, "releaseRelocatableHeapBlockMetadata", RELEASE, {"u16"}, symbols)
    globs = {u.subject: u for u in found if u.kind == "undeclared_global"}
    assert "global:0x80110918" in globs
    g = globs["global:0x80110918"]
    assert g.capabilities["accessed"] == [{"offset": 0, "width": 2,
                                           "signed": 0}]
    assert g.capabilities["loaded"] and g.capabilities["stored"]
    assert g.cites == (9001, 9002)          # provenance, not a bare claim


def test_subscripted_global_raises_an_unpinnable_extent():
    conn = _Conn(layout_rows=[], global_rows=[(1, 0, 4, None, 1)])
    symbols = {"gRelocatableHeapFreeBlockStack": 0x801107D8,
               "gRelocatableHeapUsedBlockCount": 0x80110918}
    found = unknowns.enumerate_unknowns(
        conn, "releaseRelocatableHeapBlockMetadata", RELEASE, {"u16"}, symbols)
    stack = [u for u in found if u.subject == "global:0x801107D8"]
    assert any(u.kind == "field_extent" and u.status == "unpinnable"
               for u in stack)
    glob = next(u for u in stack if u.kind == "undeclared_global")
    assert glob.capabilities["subscripted"] == ["gRelocatableHeapFreeBlockStack"]


def test_do_token_raises_a_loop_form_unknown():
    code = 'void f(void) {\n    do {\n        x += 1;\n    } while (x < 4);\n}\n'
    found = unknowns.enumerate_unknowns(_Conn(), "f", code, set(), {})
    assert any(u.kind == "loop_form" and u.resolver == "solver.rewrites"
               for u in found)


def test_ranking_weights_unresolvable_above_many_resolvable():
    """One branch_shape must outrank a dozen struct layouts."""
    hard = [unknowns.Unknown("f:body", "branch_shape", "free")]
    easy = [unknowns.Unknown(f"f:param{i}", "struct_layout", "free")
            for i in range(12)]
    assert unknowns.free_weight(hard) > unknowns.free_weight(easy)


def test_unpinnable_rows_do_not_inflate_the_queue():
    rows = [unknowns.Unknown("f:p.a", "field_name", "unpinnable")] * 20
    assert unknowns.free_weight(rows) == 0


def test_persist_refuses_an_uncited_claim():
    """CLAUDE.md rule 4, enforced rather than trusted."""
    db = sqlite3.connect(":memory:")
    db.execute("create table inference (id integer primary key, kind text,"
               " subject text, value text, confidence real, origin text,"
               " status text, created_at integer)")
    db.execute("create table inference_support (inference_id integer,"
               " evidence_id integer)")
    bad = [unknowns.Unknown("global:0x1", "undeclared_global", "known")]
    with pytest.raises(ValueError, match="uncited"):
        unknowns.persist(db, bad, "test")


def test_persist_writes_support_rows():
    db = sqlite3.connect(":memory:")
    db.execute("create table inference (id integer primary key, kind text,"
               " subject text, value text, confidence real, origin text,"
               " status text, created_at integer)")
    db.execute("create table inference_support (inference_id integer,"
               " evidence_id integer)")
    good = [unknowns.Unknown("global:0x1", "undeclared_global", "free",
                             {"loaded": True}, "miner.globals_layout",
                             (7, 8))]
    assert unknowns.persist(db, good, "test") == 1
    assert db.execute("select count(*) from inference_support").fetchone()[0] == 2
    value = db.execute("select value from inference").fetchone()[0]
    assert json.loads(value) == {"loaded": True}


def test_symbol_table_ignores_the_size_annotation(tmp_path):
    """Join on address; `// size:` is the decomp team's curation, not evidence."""
    (tmp_path / "symbol_addrs.txt").write_text(
        "gRacePlayerHitCueId = 0x80121D50; // size:0x4\n", encoding="utf-8")
    table = unknowns.symbol_table(tmp_path)
    assert table == {"gRacePlayerHitCueId": 0x80121D50}


def test_global_base_uppercase_hex_is_matched():
    """Regression: the evidence tier spells bases global:0x801107D8.

    Formatting with {addr:#010x} produced lowercase and matched only the 133
    of 1,166 addresses with no hex letters, silently returning zero citations
    for the other 1,033. It did not error; it under-reported.
    """
    seen = {}

    class _Recorder:
        def execute(self, sql, params=()):
            if "func_addr" not in sql:
                seen["params"] = params

            class _Cur(list):
                def fetchall(self):
                    return []
            return _Cur()

    unknowns._global_evidence(_Recorder(), 0x801107D8)
    assert "global:0x801107D8" in seen["params"]


BLANK = '''#include "common.h"

// file is blank because m2c failed to decompile function
'''


def test_absent_body_is_the_maximal_unknown_not_the_minimal():
    """m2c declining leaves nothing to enumerate, which is not the same as
    nothing being unknown. Ranking those first was the bug gate 2 caught."""
    found = unknowns.enumerate_unknowns(_Conn(), "noopThreeArgs", BLANK,
                                        set(), {})
    assert [u.kind for u in found] == ["absent_body"]
    assert found[0].status == "free"
    assert unknowns.free_weight(found) == 100
    # strictly worse than a function with a dozen resolvable layout unknowns
    easy = [unknowns.Unknown(f"f:param{i}", "struct_layout", "free")
            for i in range(12)]
    assert unknowns.free_weight(found) > unknowns.free_weight(easy)
