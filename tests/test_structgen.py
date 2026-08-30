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


def test_repad_reproduces_the_slideout_fix():
    """The one-number change that took SlideOut 99.999 -> byte-exact."""
    body = "{\n u8 _pad0[0x1C];\n s32 field1C;\n u8 _pad1[0x4];\n s32 field28;\n}"
    new, changed = structgen.repad(body, {0x1C: 4, 0x28: 4})
    assert changed
    assert "_pad1[0x8]" in new, new


def test_repad_never_deletes_unobserved_fields():
    """Regenerating from ONE function's evidence deleted posX/posY/posZ/rotY
    and broke the body. A struct is a program-wide fact; per-function evidence
    cannot reconstruct one, so fields are never removed."""
    body = ("{\n u8 _pad0[0x18];\n int posX;\n int posY;\n int posZ;\n"
            " s16 field24;\n s16 rotY;\n u16 textureId;\n}")
    new, _ = structgen.repad(body, {0x18: 4, 0x24: 2})
    for f in ("posX", "posY", "posZ", "field24", "rotY", "textureId"):
        assert f in new, f"{f} was deleted"


def test_repad_is_a_no_op_when_offsets_already_line_up():
    body = ("{\n u8 _pad0[0x18];\n int posX;\n int posY;\n int posZ;\n"
            " s16 field24;\n}")
    _, changed = structgen.repad(body, {0x18: 4, 0x24: 2})
    assert changed is False, "must not churn a struct that is already correct"


def test_repad_inserts_when_a_field_is_short_of_its_offset():
    """This test previously asserted changed is False, encoding repad's
    inability to INSERT padding as though it were correct behaviour. It was a
    limitation, not a specification: prefilled candidates emit no pad members
    at all, and repad found 0 of 22 to repair while reporting "padding already
    right". fieldFF belongs at 0xFF and must be moved there."""
    body = "{\n s32 field00;\n s32 fieldFF;\n}"
    new, changed = structgen.repad(body, {0x00: 4, 0xFF: 4})
    assert changed is True
    assert "pad04[0xfb]" in new, new          # 0x04 + 0xfb == 0xff


def test_repad_INSERTS_padding_where_there_is_none():
    """Prefilled candidates write named-by-offset fields with no pad members at
    all, so they land at 0, 4, 8. Resizing cannot fix that -- there is nothing
    to resize -- and the first version silently reported "padding already
    right" for exactly this case, then found 0 of 22 candidates to repair."""
    body = "{\n    s32 field1C;\n    s32 field24;\n    s16 field502;\n}"
    new, changed = structgen.repad(body, {0x1C: 4, 0x24: 4, 0x502: 2})
    assert changed
    assert "pad00[0x1c]" in new, new
    assert "pad20[0x4]" in new, new
    assert "pad28[0x4da]" in new, new       # 0x28 + 0x4da == 0x502


def test_inserted_padding_puts_every_field_on_its_offset():
    body = "{\n    s32 field1C;\n    s32 field24;\n    s16 field502;\n}"
    new, _ = structgen.repad(body, {0x1C: 4, 0x24: 4, 0x502: 2})
    import re
    cursor = 0
    sizes = {"s32": 4, "s16": 2, "char": 1}
    for m in re.finditer(r"(\w+)\s+(\w+)(?:\[(0x[0-9a-f]+)\])?\s*;", new):
        ctype, name, arr = m.group(1), m.group(2), m.group(3)
        n = int(arr, 16) if arr else 1
        if name.startswith("pad"):
            cursor += sizes[ctype] * n
            continue
        want = int(name[5:], 16)
        assert cursor == want, f"{name} at {cursor:#x}, should be {want:#x}"
        cursor += sizes[ctype] * n


# ------------------------- offsets declared in comments (near-miss repair)

def test_repad_reads_offset_from_a_trailing_comment():
    """A semantic field name with the offset in a comment.

    updateTimeTrialRecordDeltaPopupSlideIn sat at 99.999 with one fault: a
    field its own comment placed at 0x28 actually landed at 0x20. repad read
    offsets only from NAMES, so it reported "no change" and the candidate
    could not be finished. Reading the comment closes it -- byte-exact.
    """
    body = ("typedef struct {\n"
            "    char _pad0[0x18];\n"
            "    s16  timer;         /* 0x18 : timer */\n"
            "    char _pad1[2];      /* 0x1A - 0x1B : padding */\n"
            "    s32  x;             /* 0x1C : X position */\n"
            "    s32  velocity;      /* 0x28 : velocity */\n"
            "} T;\n")
    out, changed = structgen.repad(body, {0x18: 2, 0x1C: 4, 0x28: 4})
    assert changed
    assert "0x8" in out or "[8]" in out


def test_comment_offset_must_agree_with_the_evidence():
    """A comment is the model's claim, not a fact.

    If the binary never observes that offset, the claim carries no weight and
    repad must not act on it -- otherwise a confident wrong comment silently
    rewrites the struct.
    """
    body = ("typedef struct {\n"
            "    s32  a;             /* 0x00 */\n"
            "    s32  bogus;         /* 0x99 : not observed anywhere */\n"
            "} T;\n")
    out, changed = structgen.repad(body, {0x00: 4})
    assert not changed
    assert out == body


def test_comment_range_uses_the_first_offset_only():
    """`/* 0x1A - 0x1B : padding */` names a range; 0x1A is the field."""
    assert structgen._offset_from_comment(
        "x;   /* 0x1A - 0x1B : padding */", 2, {0x1A: 2}) == 0x1A


def test_field_name_offset_still_wins_over_comment():
    body = ("typedef struct {\n"
            "    s32 field1C;        /* 0x99 : wrong comment */\n"
            "} T;\n")
    # the name says 0x1C and the evidence has it; the comment must not override
    out, _changed = structgen.repad(body, {0x1C: 4})
    assert "0x99" not in out.replace("/* 0x99 : wrong comment */", "")


# --------------------------- positional alignment (propose, oracle verifies)

def test_positional_alignment_inserts_padding_by_order():
    """Fields in the right order, wrong offsets -- the common shape."""
    body = ("typedef struct {\n"
            "    s32 a;\n"
            "    s32 b;\n"
            "} T;\n")
    out, changed = structgen.align_positional(body, {0x0: 4, 0x10: 4})
    assert changed
    assert "pad04[0xc]" in out


def test_positional_alignment_refuses_on_width_mismatch():
    """An s32 onto a byte the program only reads as u8 is a DIFFERENT field.

    Accepting it would manufacture nonsense for the oracle to reject, and
    would hide the real mapping.
    """
    body = "typedef struct {\n    s32 a;\n} T;\n"
    out, changed = structgen.align_positional(body, {0x0: 1})
    assert not changed and out == body


def test_positional_alignment_refuses_to_move_a_field_backwards():
    body = ("typedef struct {\n"
            "    s32 a;\n"
            "    s32 b;\n"
            "} T;\n")
    # second observed offset is before the first field's end
    out, changed = structgen.align_positional(body, {0x0: 4, 0x2: 4})
    assert not changed


def test_positional_alignment_needs_enough_observed_offsets():
    body = ("typedef struct {\n"
            "    s32 a;\n"
            "    s32 b;\n"
            "    s32 c;\n"
            "} T;\n")
    out, changed = structgen.align_positional(body, {0x0: 4, 0x8: 4})
    assert not changed


def test_positional_alignment_skip_drops_leading_offsets():
    body = "typedef struct {\n    s32 a;\n} T;\n"
    # with skip=1 the field maps to 0x10, not 0x0
    out, changed = structgen.align_positional(body, {0x0: 4, 0x10: 4}, skip=1)
    assert changed and "pad00[0x10]" in out


def test_positional_alignment_is_a_no_op_when_already_correct():
    body = ("typedef struct {\n"
            "    s32 a;\n"
            "    s32 b;\n"
            "} T;\n")
    out, changed = structgen.align_positional(body, {0x0: 4, 0x4: 4})
    assert not changed and out == body


def test_positional_alignment_ignores_existing_padding_fields():
    body = ("typedef struct {\n"
            "    char _pad0[0x4];\n"
            "    s32 a;\n"
            "} T;\n")
    out, changed = structgen.align_positional(body, {0x0: 4})
    # the only real field maps to 0x0; nothing to insert before it
    assert not changed


# ------------------- subsequence alignment (candidate declares a SUBSET)

def test_subsequence_alignment_skips_undeclared_fields():
    """10 declarations against 29 offsets is normal; field i is not offset i."""
    body = ("typedef struct {\n"
            "    s32 a;\n"
            "    s16 b;\n"
            "} T;\n")
    # the s16 must land on 0x10, the only halfword, not on offset index 1
    out, changed = structgen.align_subsequence(
        body, {0x0: 4, 0x4: 4, 0x8: 4, 0x10: 2})
    assert changed
    assert "pad04[0xc]" in out


def test_subsequence_alignment_requires_a_compatible_width():
    body = "typedef struct {\n    f32 x;\n} T;\n"
    out, changed = structgen.align_subsequence(body, {0x0: 1, 0x2: 2})
    assert not changed and out == body


def test_subsequence_alignment_preserves_declaration_order():
    body = ("typedef struct {\n"
            "    s16 a;\n"
            "    s16 b;\n"
            "} T;\n")
    out, changed = structgen.align_subsequence(body, {0x0: 2, 0x8: 2})
    assert changed
    # b goes after a, never before it
    assert out.index("s16 a;") < out.index("s16 b;")


def test_subsequence_alignment_no_op_when_already_correct():
    body = ("typedef struct {\n"
            "    s32 a;\n"
            "    s32 b;\n"
            "} T;\n")
    out, changed = structgen.align_subsequence(body, {0x0: 4, 0x4: 4})
    assert not changed


def test_subsequence_last_variant_is_a_different_proposal():
    body = "typedef struct {\n    s32 a;\n} T;\n"
    first, c1 = structgen.align_subsequence(body, {0x0: 4, 0x20: 4}, "first")
    last, c2 = structgen.align_subsequence(body, {0x0: 4, 0x20: 4}, "last")
    # first puts it at 0x0 (no edit needed); last puts it at 0x20
    assert not c1
    assert c2 and "pad00[0x20]" in last
