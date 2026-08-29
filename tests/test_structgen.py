"""Tests for the struct synthesiser.

Every test corresponds to a bug that actually occurred while building it.
"""

import sqlite3

import pytest

from solver import structgen

SCHEMA = """
CREATE TABLE functions (addr INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, func_addr INTEGER,
    base TEXT, base_reg TEXT, offset INTEGER, width INTEGER,
    signed INTEGER, is_load INTEGER);
"""


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA)
    c.execute("INSERT INTO functions (addr, name) VALUES (1, 'f')")
    for base, off, w, sg in [("param0", 24, 2, None), ("param0", 28, 4, 1),
                             ("param0", 40, 4, 1), ("stack", -4, 4, 1),
                             ("global:0x80001234", 0, 4, 1)]:
        c.execute("INSERT INTO evidence (kind, func_addr, base, offset, width,"
                  " signed, is_load) VALUES ('mem_access',1,?,?,?,?,0)",
                  (base, off, w, sg))
    c.commit()
    return c


def test_kind_is_mem_access_not_access(conn):
    """The first query used kind='access', matched zero rows, and reported
    layout=0 for every function -- which reads as 'no data' rather than
    'broken filter'."""
    lay = structgen.layout(conn, "f")
    assert lay, "layout is empty: the evidence query is wrong, not the data"
    assert "param0" in lay


def test_stack_and_globals_are_excluded(conn):
    lay = structgen.layout(conn, "f")
    assert set(lay) == {"param0"}, "stack/global bases are not struct fields"


def test_offsets_land_exactly_where_observed(conn):
    """Gaps must become padding. Closing them up IS the bug being fixed."""
    out = structgen.render("A", structgen.layout(conn, "f")["param0"])
    assert "char pad00[0x18];" in out
    assert "s16 field_18;" in out
    assert "char pad1a[0x2];" in out
    assert "s32 field_1c;" in out
    assert "char pad20[0x8];" in out
    assert "s32 field_28;" in out


def test_matches_the_layout_that_produced_a_byte_exact_match(conn):
    """This exact layout took updateTimeTrialRecordDeltaPopupSlideIn from
    99.61 to 100.00."""
    fields = structgen.layout(conn, "f")["param0"]
    assert fields == [(24, 2, "s16"), (28, 4, "s32"), (40, 4, "s32")]


def test_struct_names_does_not_match_break_statements():
    """r"\\}\\s*(\\w+)\\s*;" returned ['break'], because \\s* spans newlines and

           }
           break;

    looks like a struct named break. That made functions with NO structs
    report as 'rewrite applied and did not help'."""
    code = "void f(void) {\n    switch (x) {\n    case 1:\n    }\n    break;\n}"
    assert structgen.struct_names(code) == []


def test_struct_names_finds_a_real_typedef():
    code = "typedef struct {\n int a;\n} Thing;\nvoid f(void){}"
    assert structgen.struct_names(code) == ["Thing"]


def test_rewrite_reports_whether_it_applied():
    """A silent no-op must be detectable: this project has shipped 'patched'
    edits that never applied."""
    code = "typedef struct {\n int a;\n} Thing;\n"
    _, changed = structgen.rewrite(code, "Thing", [(0, 4, "s32")])
    assert changed is True
    _, changed = structgen.rewrite(code, "Absent", [(0, 4, "s32")])
    assert changed is False


def test_rewrite_tolerates_stray_whitespace():
    """An exact-text match already failed once on a space after 'struct'."""
    code = "typedef struct \n{\n  u16 t;\n}  Thing ;\n"
    _, changed = structgen.rewrite(code, "Thing", [(0, 2, "s16")])
    assert changed is True


def test_unknown_signedness_defaults_to_signed(conn):
    """IDO's plain lw/lh/lb are signed loads, so signed is the shape the
    compiler assumes absent evidence."""
    assert structgen.field_type(2, None) == "s16"
    assert structgen.field_type(2, 0) == "u16"


def test_rewrite_preserves_existing_field_names():
    """Renaming fields breaks the body, which still uses the old names.

    On updateTimeTrialRecordDeltaPopupSlideOut the rewrite renamed field28 to
    field_28, the candidate stopped compiling, and the harness reported that as
    "no improvement" -- hiding a fix one padding byte from byte-exact.
    """
    code = ("typedef struct Actor\n{\n  u8 _pad0[0x1C];\n  s32 field1C;\n"
            "  u8 _pad1[0x4];\n  s32 field28;\n} Actor;\n"
            "void f(Actor *a){ a->field28 = a->field1C; }")
    new, changed = structgen.rewrite(code, "Actor",
                                     [(0x1C, 4, "s32"), (0x28, 4, "s32")])
    assert changed
    assert "field1C" in new and "field28" in new, \
        "original field names must survive or the body stops compiling"
    assert "field_1c" not in new and "field_28" not in new
    # and the padding must be corrected to place field28 at 0x28
    assert "char pad20[0x8];" in new, new


def test_padding_places_fields_at_the_observed_offsets():
    """The SlideOut bug exactly: 0x1C + 4 + pad(4) lands field28 at 0x24."""
    out = structgen.render("A", [(0x1C, 4, "s32"), (0x28, 4, "s32")])
    assert "char pad00[0x1c];" in out
    assert "char pad20[0x8];" in out, "gap 0x20..0x27 must be 8 bytes, not 4"


def test_pad_fields_are_not_treated_as_named_fields():
    body = "{ u8 _pad0[0x1C]; s32 field1C; }"
    keep = structgen.preserve_names(body, [(0x1C, 4, "s32")])
    assert keep == {0x1C: "field1C"}, keep
