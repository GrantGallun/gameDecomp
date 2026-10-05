"""Confirmed two-default branch rewrite from the retained controller-pak candidate."""

from pathlib import Path

from solver import branch_defaults


ARTIFACTS = Path(__file__).resolve().parent / "fixtures/branch_defaults"
FUNCTION = "drawControllerPakFileDeleteConfirmOptions"


def test_retained_candidate_produces_exact_winning_source():
    baseline = (ARTIFACTS / "baseline.c").read_text()
    winner = (ARTIFACTS / "defaults_in_arms.c").read_text()
    variants = list(branch_defaults.variants(baseline, FUNCTION))
    assert any(code == winner for _, _, code in variants)


def test_renamed_scalar_locals_keep_their_values_in_both_arms():
    source = """void f(void) {
    unsigned short left;
    unsigned short right;
    left = 7;
    right = 9;
    if (choice == 0) {
        left = 11;
    } else {
        right = 13;
    }
    use(left, right);
}
"""
    expected = """void f(void) {
    unsigned short left;
    unsigned short right;
    if (choice == 0) {
        left = 11;
        right = 9;
    } else {
        left = 7;
        right = 13;
    }
    use(left, right);
}
"""
    assert expected in [code for _, _, code in branch_defaults.variants(source, "f")]


def test_declines_condition_using_local_or_call_and_escaped_address():
    template = """void f(void) {
    int a;
    int b;
    a = 1;
    b = 2;
    if (COND) {
        a = 3;
    } else {
        b = 4;
    }
    TAIL
}
"""
    for condition, tail in (("a == 0", "use(a, b);"),
                            ("next()", "use(a, b);"),
                            ("choice == 0", "escape(&a);")):
        source = template.replace("COND", condition).replace("TAIL", tail)
        assert list(branch_defaults.variants(source, "f")) == []


def test_declines_comments_and_volatile_or_shadowed_locals():
    source = """void f(void) {
    int a;
    int b;
    a = 1;
    b = 2; /* attached comment */
    if (choice == 0) {
        a = 3;
    } else {
        b = 4;
    }
}
"""
    assert list(branch_defaults.variants(source, "f")) == []
    assert list(branch_defaults.variants(source.replace("int a;", "volatile int a;").replace("; /* attached comment */", ";"), "f")) == []
    assert list(branch_defaults.variants(source.replace("; /* attached comment */", ";").replace("int a;", "int a;\n    { int a; }"), "f")) == []


def test_declines_defaults_guarded_by_braceless_parent():
    source = """void f(void) {
    int a;
    int b;
    if (enabled)
        a = 1;
        b = 2;
        if (choice == 0) {
            a = 3;
        } else {
            b = 4;
        }
}
"""
    assert list(branch_defaults.variants(source, "f")) == []
    assert list(branch_defaults.variants(source.replace("if (enabled)", "if (enabled) use();\n    else"), "f")) == []


def test_declines_nested_declaration_when_site_uses_global():
    source = """int a;
void f(void) {
    int b;
    if (other) {
        int a;
        a = 0;
    }
    a = 1;
    b = 2;
    if (choice == 0) {
        a = 3;
    } else {
        b = 4;
    }
}
"""
    assert list(branch_defaults.variants(source, "f")) == []


def test_declines_preprocessor_directive_in_function_body():
    source = """void f(void) {
    int a;
    int b;
#define a other
    a = 1;
    b = 2;
    if (choice == 0) {
        a = 3;
    } else {
        b = 4;
    }
}
"""
    assert list(branch_defaults.variants(source, "f")) == []


def test_declines_for_header_with_braceless_first_default():
    source = """void f(void) {
    int a;
    int b;
    int i;
    a = 0;
    for (i = 0; i < 0; ++i)
    a = 1;
    b = 2;
    if (choice == 0) {
        a = 3;
    } else {
        b = 4;
    }
}
"""
    assert list(branch_defaults.variants(source, "f")) == []


def test_declines_condition_macro_that_expands_to_local_read():
    source = """#define CHOICE (a == 0)
void f(void) {
    int a;
    int b;
    a = 1;
    b = 2;
    if (CHOICE) {
        a = 3;
    } else {
        b = 4;
    }
}
"""
    assert list(branch_defaults.variants(source, "f")) == []
