from solver import local_web_merge
from solver import regalloc_mutations


MOTIVATING_SHAPE = """void f(int condition, void *value) {
    void **head_value;
    void **tail_value;

    if (condition == 0) {
        head_value = value;
        use(head_value);
    } else {
        tail_value = value;
        use(tail_value);
    }
}
"""


def test_merges_compatible_pointer_webs_in_mutually_exclusive_if_arms():
    rows = list(local_web_merge.variants(MOTIVATING_SHAPE, "f"))

    assert len(rows) == 1
    label, family, candidate = rows[0]
    assert label.startswith("local_web_merge:")
    assert family == "local_web_merge"
    assert "void **tail_value;" not in candidate
    assert "tail_value" not in candidate
    assert candidate.count("head_value") == 5


def test_local_web_merge_is_in_the_regular_candidate_stream():
    rows = list(regalloc_mutations.variants(MOTIVATING_SHAPE, "f"))

    assert any(kind == "local_web_merge" and candidate.count("head_value") == 5
               and "tail_value" not in candidate for _label, kind, candidate in rows)


def test_declines_nonexclusive_arms_and_different_pointer_types():
    nonexclusive = MOTIVATING_SHAPE.replace(
        "    } else {\n        tail_value = value;\n        use(tail_value);\n    }",
        "    }\n    tail_value = value;\n    use(tail_value);")
    different_type = MOTIVATING_SHAPE.replace("void **tail_value;", "char **tail_value;")

    assert list(local_web_merge.variants(nonexclusive, "f")) == []
    assert list(local_web_merge.variants(different_type, "f")) == []


def test_declines_shadowed_address_escaped_or_array_locals():
    shadowed = MOTIVATING_SHAPE.replace(
        "        tail_value = value;\n        use(tail_value);",
        "        void **tail_value;\n        tail_value = value;\n        use(tail_value);")
    inline_shadow = MOTIVATING_SHAPE.replace(
        "    } else {\n        tail_value = value;",
        "    } else { void **tail_value;\n        tail_value = value;")
    address_escaped = MOTIVATING_SHAPE.replace("use(tail_value);", "use(&tail_value);")
    array_local = MOTIVATING_SHAPE.replace("void **tail_value;", "void *tail_value[2];")
    member_field = MOTIVATING_SHAPE.replace("use(tail_value);", "use(object.tail_value);")

    for source in (shadowed, inline_shadow, address_escaped, array_local, member_field):
        assert list(local_web_merge.variants(source, "f")) == []


def test_rewrites_only_owned_local_uses_and_preserves_other_scopes_and_members():
    source = MOTIVATING_SHAPE + """void unrelated(void) {
    int tail_value;
    tail_value = 7;
    object.tail_value = tail_value;
}
"""

    [(_label, _family, candidate)] = list(local_web_merge.variants(source, "f"))

    assert "int tail_value;\n    tail_value = 7;\n    object.tail_value = tail_value;" in candidate


def test_declines_nested_scalar_shadow_and_persistent_or_const_pointer_locals():
    scalar_shadow = MOTIVATING_SHAPE.replace(
        "    } else {\n        tail_value = value;",
        "    } else {\n        int tail_value;\n        tail_value = 0;\n        tail_value = value;")
    persistent = MOTIVATING_SHAPE.replace("void **tail_value;", "static void **tail_value;")
    immutable = MOTIVATING_SHAPE.replace("void **tail_value;", "const void **tail_value;")

    for source in (scalar_shadow, persistent, immutable):
        assert list(local_web_merge.variants(source, "f")) == []
