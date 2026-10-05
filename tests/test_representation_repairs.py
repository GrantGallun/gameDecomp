"""Real residuals must be reachable through the normal mutation interface."""
from pathlib import Path
import pytest
from solver import regalloc_mutations

FIXTURES = Path(__file__).parent / "fixtures"
SOURCE = (FIXTURES / "vibrato_conversion.c").read_text()
DIFF = (FIXTURES / "vibrato_conversion.diff").read_text()


def proposals(source=SOURCE, diff=DIFF, family=None):
    rows = list(regalloc_mutations.variants(source, "Fvibup", diff))
    return [code for _, kind, code in rows if kind == family]


def test_unsigned_conversion_collapses_motivating_scaffold_and_inlines_values():
    rows = proposals(family="unsigned_float")
    assert rows
    assert any("4294967296" not in code and "(f32) (u32)" in code
               and "var_f6 =" not in code and "temp_t8 =" not in code for code in rows)


def test_cursor_rebase_preserves_byte_addresses_and_adjusts_return():
    rows = proposals(family="cursor_rebase")
    assert len(rows) == 1
    assert "arg1 = (u8 *)arg1 + 2;" in rows[0]
    assert "((u8 *)arg1) + 0x2" not in rows[0]
    assert "return ((u8 *)arg1) + 0 + 1;" in rows[0]
    assert not proposals(rows[0], family="cursor_rebase")


def test_repairs_compose_in_either_order_without_hardcoded_function_names():
    conversion = proposals(family="unsigned_float")
    assert conversion and proposals(conversion[0], family="cursor_rebase")
    cursor = proposals(family="cursor_rebase")
    assert cursor and proposals(cursor[0], family="unsigned_float")
    renamed = SOURCE.replace("Fvibup", "another").replace("temp_t8", "value").replace("var_f6", "floating")
    rows = list(regalloc_mutations.variants(renamed, "another", DIFF))
    assert {kind for _, kind, _ in rows} >= {"unsigned_float", "cursor_rebase"}


@pytest.mark.parametrize("old,new", [
    ("4294967296.0f", "2147483648.0f"),
    ("temp_t8 < 0", "temp_t8 > 0"),
    ("u8 temp_t8", "volatile u8 temp_t8"),
    ("    return", "    temp_t8++;\n    return"),
    ("    (*(f32 *)", "    side_effect();\n    (*(f32 *)"),
])
def test_unsigned_conversion_declines_changed_or_nonlocal_scaffolds(old, new):
    assert old in SOURCE
    assert not proposals(SOURCE.replace(old, new), family="unsigned_float")


@pytest.mark.parametrize("source,diff", [
    (SOURCE, ""),
    (SOURCE, DIFF.replace("addiu    a1,a1,2", "addiu    a0,a0,2")),
    (SOURCE.replace("    temp_t8 =", "    escape(arg1);\n    temp_t8 ="), DIFF),
    (SOURCE.replace("    temp_t8 =", "    if (flag)\n    temp_t8 ="), DIFF),
    (SOURCE.replace("((u8 *)arg1)", "((u16 *)arg1)"), DIFF),
    (SOURCE.replace("void *arg1", "u64 arg1"), DIFF),
])
def test_cursor_rebase_requires_bound_stride_and_complete_byte_views(source, diff):
    assert not proposals(source, diff, "cursor_rebase")


def test_existing_byte_stride_repair_is_reachable_in_normal_search():
    source = "void f(void) {\n    s16 *p;\n    p += 2;\n}\n"
    diff = "-addiu s0,s0,2\n+addiu s0,s0,4\n"
    rows = list(regalloc_mutations.variants(source, "f", diff))
    assert any(kind == "residual_evidence" and "(unsigned char *)p + 2" in code
               for _, kind, code in rows)


@pytest.mark.parametrize("expression", [
    "flag ? var_f6 : 0.0f", "flag && var_f6", "flag || var_f6",
])
def test_conversion_does_not_make_an_unconditional_read_conditional(expression):
    code = SOURCE.replace("(f64) var_f6 / 50.0", expression)
    assert not proposals(code, family="unsigned_float")


def test_conversion_does_not_emit_masked_character_literal_as_an_empty_value():
    code = SOURCE.replace("(*(u8 *)((unsigned char *)((u8 *)arg1) + 0x2))", "'A'")
    assert not proposals(code, family="unsigned_float")


def test_cursor_does_not_insert_assignment_before_a_c89_declaration():
    code = "void *Fvibup(void *a, void *arg1) {\n    u8 x = *((u8 *)((u8 *)arg1) + 2);\n    return ((u8 *)arg1) + 3;\n}\n"
    assert not proposals(code, DIFF, "cursor_rebase")
