"""The missing second edit that closes the frozen Fdistort development state."""
from solver import regalloc_mutations


SOURCE = """s32 decode(State *out, u8 *cursor) {
    s32 value;
    value = *cursor;
    if (value & 0x80) {
        value |= ~0xFF;
    }
    out->pitch = (f32)((f64)(f32)value / 100.0);
    return (s32)(cursor + 1);
}
"""


def variants(source=SOURCE, function="decode"):
    return [text for _, kind, text in regalloc_mutations.variants(source, function)
            if kind == "cursor_advance"]


def test_fires_through_real_mutation_registry_on_motivating_residual():
    rows = variants()
    assert len(rows) == 1
    assert "value = *cursor++;" in rows[0]
    assert "return (s32)(cursor);" in rows[0]
    assert "value |= ~0xFF;" in rows[0]


def test_names_and_return_cast_are_not_special_cased():
    source = SOURCE.replace("decode", "next_sample").replace("cursor", "bytes")
    source = source.replace("s32 next_sample", "u8 *next_sample").replace("(s32)(bytes + 1)", "bytes + 1")
    assert "return bytes;" in variants(source, "next_sample")[0]


def test_declines_when_cursor_used_or_modified_in_between():
    for statement in ("consume(cursor);", "cursor += 2;", "escape(&cursor);", "return 0;"):
        assert not variants(SOURCE.replace("    if (value", f"    {statement}\n    if (value"))


def test_declines_conditional_load_and_volatile_pointer():
    assert not variants(SOURCE.replace("value = *cursor;", "if (out) { value = *cursor; }"))
    assert not variants(SOURCE.replace("u8 *cursor", "u8 *volatile cursor"))


def test_declines_unbraced_conditional_final_return():
    assert not variants(SOURCE.replace("    return", "    if (value) return"))
    assert not variants(SOURCE.replace("    return", "    if (value)\n        return"))


def test_cast_must_wrap_pointer_addition_not_change_its_units():
    source = SOURCE.replace("u8 *cursor", "u16 *cursor")
    assert not variants(source.replace("(s32)(cursor + 1)", "(s32)cursor + 1"))
    assert not variants(source.replace("(s32)(cursor + 1)", "((s32)cursor + 1)"))
    assert variants(source.replace("(s32)(cursor + 1)", "((s32)(cursor + 1))"))


def test_comments_are_preserved_and_do_not_supply_matches():
    source = SOURCE.replace("    value = *cursor;", "    /* cursor + 1 is a comment */\n    value = *cursor;")
    assert "/* cursor + 1 is a comment */" in variants(source)[0]
    assert not variants(SOURCE.replace("value = *cursor;", "/* value = *cursor; */ value = 0;"))


def test_other_functions_and_pointer_units_remain_intact():
    other = "u16 *other(u16 *cursor) { return cursor + 1; }\n"
    row = variants(other + SOURCE.replace("u8 *cursor", "u16 *cursor"))[0]
    assert row.startswith(other)
    assert "value = *cursor++;" in row
