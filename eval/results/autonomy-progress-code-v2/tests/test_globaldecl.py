"""globaldecl must FIRE on releaseRelocatableHeapBlockMetadata."""

from solver import globaldecl


class _Conn:
    """unknowns._global_evidence's query only, keyed by the base in params."""

    def __init__(self, by_base):
        self._by_base = by_base

    def execute(self, _sql, params=()):
        rows = []
        for key in params:
            rows.extend(self._by_base.get(key, []))

        class _Cur(list):
            def fetchall(self):
                return list(self)

        return _Cur(rows)


RELEASE = '''#include "common.h"

void releaseRelocatableHeapBlockMetadata(RelocatableHeapBlock *block) {
    u16 temp_t7;

    temp_t7 = gRelocatableHeapUsedBlockCount - 1;
    gRelocatableHeapUsedBlockCount = temp_t7;
    gRelocatableHeapFreeBlockStack[temp_t7 & 0xFFFF] = block;
    block->status = 0;
}
'''

SYMBOLS = {"gRelocatableHeapUsedBlockCount": 0x80110918,
           "gRelocatableHeapFreeBlockStack": 0x801107D8}
EVIDENCE = {
    "global:0x80110918": [(1, 0, 2, 0, 1), (2, 0, 2, 0, 0)],
    "global:0x801107D8": [(3, 0, 4, None, 0)],
}


def test_fires_on_the_motivating_draft():
    conn = _Conn(EVIDENCE)
    plans = globaldecl.plan(conn, RELEASE, "releaseRelocatableHeapBlockMetadata",
                            {"u16"}, SYMBOLS)
    by_name = {p["name"]: p for p in plans}
    assert set(by_name) == set(SYMBOLS)
    assert by_name["gRelocatableHeapUsedBlockCount"]["text"] == (
        "extern u16 gRelocatableHeapUsedBlockCount;")     # width 2 from evidence
    assert by_name["gRelocatableHeapUsedBlockCount"]["cites"] == (1, 2)


def test_subscripted_global_becomes_an_incomplete_array():
    """Legal C89, permits indexing, commits to no extent the binary lacks."""
    conn = _Conn(EVIDENCE)
    plans = globaldecl.plan(conn, RELEASE, "releaseRelocatableHeapBlockMetadata",
                            {"u16"}, SYMBOLS)
    stack = next(p for p in plans
                 if p["name"] == "gRelocatableHeapFreeBlockStack")
    assert stack["array"] is True
    assert stack["text"] == "extern s32 gRelocatableHeapFreeBlockStack[];"
    assert "[0]" not in stack["text"] and "[1]" not in stack["text"]


def test_declarations_precede_the_function():
    conn = _Conn(EVIDENCE)
    out, plans = globaldecl.declare(
        conn, RELEASE, "releaseRelocatableHeapBlockMetadata", {"u16"}, SYMBOLS)
    assert out.index("extern u16 gRelocatableHeapUsedBlockCount;") < out.index(
        "void releaseRelocatableHeapBlockMetadata(")


def test_declines_without_evidence():
    """A name with no observed access gets no declaration, not a guessed one."""
    plans = globaldecl.plan(_Conn({}), RELEASE,
                            "releaseRelocatableHeapBlockMetadata",
                            {"u16"}, SYMBOLS)
    assert plans == []


def test_callees_are_never_declared_as_data():
    """A wrong prototype is a wrong call sequence, not a compile error."""
    code = '#include "common.h"\n\nvoid f(void) {\n    someFunc(1);\n}\n'
    conn = _Conn({"global:0x80000000": [(9, 0, 4, 1, 1)]})
    assert globaldecl.plan(conn, code, "f", set(),
                           {"someFunc": 0x80000000}) == []


def test_already_declared_globals_are_skipped():
    code = ('#include "common.h"\n\nextern u32 AI_LEN_REG;\n\n'
            "u32 f(void) {\n    return AI_LEN_REG;\n}\n")
    conn = _Conn({"global:0xA4500004": [(1, 0, 4, 0, 1)]})
    assert globaldecl.plan(conn, code, "f", set(),
                           {"AI_LEN_REG": 0xA4500004}) == []


def test_build_declared_names_are_skipped():
    conn = _Conn({"global:0x80000000": [(1, 0, 4, 1, 1)]})
    code = '#include "common.h"\n\nvoid f(void) {\n    x = Gfx;\n}\n'
    assert globaldecl.plan(conn, code, "f", {"Gfx"},
                           {"Gfx": 0x80000000}) == []


def test_apply_is_identity_without_plans():
    assert globaldecl.apply(RELEASE, []) == RELEASE
