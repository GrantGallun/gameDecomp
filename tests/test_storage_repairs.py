"""Compiler-confirmed storage residuals must be reachable by normal search."""
from pathlib import Path
import pytest
from solver import regalloc_mutations

FIXTURES = Path(__file__).parent / "fixtures"
SOURCE = (FIXTURES / "register_storage.c").read_text()
DIFF = (FIXTURES / "register_storage.diff").read_text()
ALIAS_DIFF = (FIXTURES / "address_reuse.diff").read_text()


def proposals(source=SOURCE, diff=DIFF, family="register_storage", function="__osDequeueThread"):
    return [code for _, kind, code in regalloc_mutations.variants(source, function, diff) if kind == family]


def test_storage_and_address_repairs_compose_on_the_motivating_residual():
    rows = proposals()
    both = [s for s in rows if "register OSThread **var_a2;" in s and "register OSThread *var_a3;" in s]
    assert len(both) == 1
    aliases = proposals(both[0], ALIAS_DIFF, "address_reuse")
    assert len(aliases) == 1
    assert "var_a2 = &var_a3->next;\n        var_a3 = *var_a2;" in aliases[0]
    assert not proposals(aliases[0], ALIAS_DIFF, "address_reuse")


def test_storage_repair_uses_residual_evidence_without_function_or_register_names():
    renamed = SOURCE.replace("__osDequeueThread", "unlink").replace("var_a2", "link").replace("var_a3", "node")
    assert proposals(renamed, function="unlink")
    assert not proposals(diff="")
    assert not proposals(diff=ALIAS_DIFF)
    assert "+sw    a0,4(sp)" in DIFF
    assert not proposals(diff=DIFF.replace("+sw    a0,4(sp)", " sw    a0,4(sp)"))


@pytest.mark.parametrize("mutation", [
    lambda s: s.replace("OSThread **var_a2;", "volatile OSThread **var_a2;"),
    lambda s: s.replace("    var_a2 = queue;", "    escape(&var_a2);\n    var_a2 = queue;"),
    lambda s: s.replace("    var_a2 = queue;", "    escape(&(var_a2));\n    var_a2 = queue;"),
    lambda s: s.replace("    var_a2 = queue;", "    { OSThread **var_a2; }\n    var_a2 = queue;"),
])
def test_storage_declines_volatile_and_never_registers_address_taken_or_shadowed_locals(mutation):
    assert all("register OSThread **var_a2;" not in row for row in proposals(mutation(SOURCE)))


@pytest.mark.parametrize("mutation", [
    lambda s: s.replace("var_a2 = &var_a3->next;", "var_a2 = &var_a3->next;\n        mutate(var_a3);"),
    lambda s: s.replace("var_a2 = &var_a3->next;", "var_a2 = &var_a3->other;"),
    lambda s: s.replace("var_a2 = &var_a3->next;", "var_a2 = &get_node()->next;"),
    lambda s: s.replace("OSThread *var_a3;", "volatile OSThread *var_a3;"),
    lambda s: s.replace("var_a3 = var_a3->next;", "var_a3 = var_a3->next + 1;"),
])
def test_address_reuse_requires_adjacent_same_lvalue_without_effects(mutation):
    assert not proposals(mutation(SOURCE), ALIAS_DIFF, "address_reuse")


def test_comments_and_other_functions_are_preserved():
    prefix = 'const char *note = "OSThread **var_a2;";\nvoid untouched(void) { int x; }\n'
    suffix = '\nvoid after(void) { int y; }\n'
    rows = proposals(prefix + SOURCE + suffix)
    assert rows and all(s.startswith(prefix) and s.endswith(suffix) for s in rows)
    assert all("// This is a decompilation attempt" in s for s in rows)


def test_bounded_candidates_and_idempotent_qualifiers():
    rows = proposals()
    assert 0 < len(rows) <= 9 and len(set(rows)) == len(rows)
    both = next(s for s in rows if s.count("register OSThread") == 2)
    assert not proposals(both)


def test_alias_evidence_requires_same_load_destination_and_offset():
    assert not proposals(SOURCE, "", "address_reuse")
    assert not proposals(SOURCE, ALIAS_DIFF.replace("+lw    a3,0(a3)", "+lw    t0,0(a3)"), "address_reuse")
    assert not proposals(SOURCE, ALIAS_DIFF.replace("+lw    a3,0(a3)", "+lw    a3,4(a3)"), "address_reuse")


@pytest.mark.parametrize("control", ["if (t != NULL)", "while (t != NULL)", "for (; t != NULL;)", "if (t == NULL) {} else"])
def test_alias_assignment_must_execute_before_following_load(control):
    source = SOURCE.replace("        var_a2 = &var_a3->next;", f"        {control}\n        var_a2 = &var_a3->next;")
    assert not proposals(source, ALIAS_DIFF, "address_reuse")


def test_alias_reuse_declines_unresolved_global_bases():
    source = """extern struct Node *volatile global_node;
int f(void) {
    int *q;
    int result;
    q = &global_node->value;
    result = global_node->value;
    return result;
}
"""
    assert not proposals(source, ALIAS_DIFF, "address_reuse", "f")


def test_alias_reuse_declines_multihop_fields_with_unknown_qualifiers():
    source = SOURCE.replace("var_a3->next", "var_a3->next->next")
    assert not proposals(source, ALIAS_DIFF, "address_reuse")


@pytest.mark.parametrize("declaration", ["Pointer other, var_a3;", "node_pointer var_a3;"])
def test_alias_reuse_declines_comma_or_unrecognized_shadow_declarations(declaration):
    source = SOURCE.replace("        var_a2 = &var_a3->next;", f"        {declaration}\n        var_a2 = &var_a3->next;")
    assert not proposals(source, ALIAS_DIFF, "address_reuse")


PARAMETER_SOURCE = """s32 lookup(OSThread *input) {
    OSThread *current;

    current = input;
    if (current == NULL) {
        current = running;
    }
    return current->priority;
}
"""


def test_parameter_copy_reuses_original_storage_on_motivating_shape():
    rows = proposals(PARAMETER_SOURCE, DIFF, "parameter_reuse", "lookup")
    assert len(rows) == 1
    assert "current" not in rows[0]
    assert "if (input == NULL)" in rows[0] and "return input->priority;" in rows[0]
    assert not proposals(rows[0], DIFF, "parameter_reuse", "lookup")


@pytest.mark.parametrize("old,new", [
    ("OSThread *current", "OtherThread *current"),
    ("    return current", "    consume(input);\n    return current"),
    ("    return current", "    escape(&current);\n    return current"),
    ("    current = input;", "    if (flag)\n    current = input;"),
    ("    current = input;", "    consume();\n    current = input;"),
    ("    return current", "    { OtherThread *current; }\n    return current"),
    ("->priority", "->current"),
    ("OSThread *current", "static OSThread *current"),
])
def test_parameter_reuse_declines_incompatible_observable_or_ambiguous_aliases(old,new):
    assert not proposals(PARAMETER_SOURCE.replace(old,new), DIFF, "parameter_reuse", "lookup")


def test_parameter_reuse_declines_unknown_scalar_qualifiers():
    source = """typedef volatile int Value;
Value lookup(Value input) {
    Value current;
    current = input;
    current += 1;
    return current;
}
"""
    assert not proposals(source, DIFF, "parameter_reuse", "lookup")


def test_callback_prototype_names_are_not_enclosing_parameters():
    source = """int p;
int lookup(int (*callback)(int first, int p, int last)) {
    int current;
    current = p;
    current += 1;
    return current;
}
"""
    assert not proposals(source, DIFF, "parameter_reuse", "lookup")


@pytest.mark.parametrize("tag", ["struct", "union", "enum"])
def test_parameter_reuse_preserves_separate_tag_namespaces(tag):
    source = f"""int lookup(int input) {{
    int current;
    current = input;
    return current + sizeof({tag} current);
}}
"""
    assert not proposals(source, DIFF, "parameter_reuse", "lookup")


def test_register_storage_precedes_parameter_reuse_on_pointer_walk():
    rows = list(regalloc_mutations.variants(SOURCE, "__osDequeueThread", DIFF))
    kinds = [kind for _, kind, _ in rows]
    assert kinds.index("register_storage") < kinds.index("parameter_reuse")
