"""Tree-level term rewrites (solver.term_rewrite) and Engine G in the rule miner."""
import collections

from solver import rule_miner
from solver import term_rewrite as tr

SRC = """s32 foo(s32 a, u8 *p, f32 f) {
    s32 x = a * 2 + p[1];
    x = b * c + d;
    f = f * 2;
    x = g() * 2;
    x = (a + b) * 2;
    return x + x;
}
"""


def _apply(src, lhs, rhs, limit=10):
    b, e = src.index("{") + 1, src.rindex("}")
    trees = tr.index(tr.body_trees(src, b, e))
    return tr.apply(src, trees, tr.pattern(lhs), tr.pattern(rhs), limit=limit, guard=tr.integer_guard(src))


def test_rewrite_fires_on_its_shape_and_groups_the_replacement():
    out = _apply(SRC, "E0 * 2", "E0 << 1")
    assert any("s32 x = (a << 1) + p[1];" in v for v in out)
    assert any("x = a + b << 1;" in v for v in out)            # + binds tighter than <<: no group needed
    out = _apply("void h(s32 a, s32 b) {\n    a = (a | b) * 2;\n}\n", "E0 * 2", "E0 << 1")
    assert out == ["void h(s32 a, s32 b) {\n    a = (a | b) << 1;\n}\n"]   # | binds looser: grouped


def test_matching_respects_precedence():
    # `b * c + d` is (b * c) + d: E0 + E1 binds b * c and d, never `c + d`
    out = _apply(SRC, "E0 + E1", "E1 + E0")
    assert any("x = d + b * c;" in v for v in out)
    assert not any("b * (d + c)" in v or "d + c" in v for v in out)


def test_side_effects_floats_and_distinct_placeholders_decline():
    out = _apply(SRC, "E0 * 2", "E0 << 1")
    assert not any("g() << 1" in v for v in out)               # a call may not be duplicated or moved
    assert not any("f << 1" in v for v in out)                 # integer rules never touch a float
    assert _apply("void h(s32 x) {\n    x = x + x;\n}\n", "E0 + E1", "E1 + E0") == []
    assert _apply("void h(s32 x) {\n    x = x + x;\n}\n", "E0 + E0", "E0 << 1") == ["void h(s32 x) {\n    x = x << 1;\n}\n"]


def test_literals_and_n_placeholders_match_by_value():
    src = "void h(s32 x) {\n    if ((x & 0xff) == 0) { x = 3; }\n}\n"
    assert _apply(src, "E0 == 0", "!E0")[0].count("if (!(x & 0xff))") == 1
    assert "x - 5" in _apply("void h(s32 x) {\n    x = x + -5;\n}\n", "E0 + -N0", "E0 - N0")[0]


def test_rendering_never_merges_unary_minus_into_a_decrement():
    assert tr.show(tr.pattern("-(-E0)")) == "-(-E0)"
    assert tr.show(tr.pattern("N0 - -E0")) == "N0 - -E0"
    assert tr.show(tr.pattern("(E0 + E1) * 2")) == "(E0 + E1) * 2"


def test_inverse_of_a_generated_rule_swaps_sides():
    assert rule_miner.inverse("G:E0 == 0 => !E0") == "G:!E0 => E0 == 0"
    assert rule_miner.inverse("G:E0 ? E1 : E2 => !E0 ? E2 : E1") == "G:!E0 ? E2 : E1 => E0 ? E1 : E2"


def test_generated_rule_is_offered_when_the_residual_matches_its_profile(monkeypatch):
    # The table entry `!E0 => E0 == 0` records what spelling `!x` as `x == 0` does to exact code; a candidate that
    # spells `x == 0` and shows that residual is offered the reverse.
    table = rule_miner.Table({"rules": {
        "G:!E0 => E0 == 0": {"engine": "G", "examples": 5, "profile": {"-sltiu r,r,1": 5, "class:registers": 5}},
        "G:E0 + E1 => E1 + E0": {"engine": "G", "examples": 5, "profile": {"+addu r,r,r": 5}, "pruned": True}}})
    monkeypatch.setattr(rule_miner, "features", lambda diff: collections.Counter({"-sltiu r,r,1": 1}))
    src = "s32 k(s32 x, s32 y) {\n    return (x == 0) + y;\n}\n"
    props = rule_miner.proposals(src, "k", "unused", table=table)
    assert props and props[0][1] == "G:E0 == 0 => !E0"
    assert "return (!x) + y;" in props[0][3]
    assert all(not p[1].startswith("G:E1 + E0") and not p[1].startswith("G:E0 + E1") for p in props)


def test_generated_rules_keep_reserved_slots_against_higher_scoring_templates(monkeypatch):
    # G3 frame (2026-09-30): G applied in 37 of 50 functions but ranked in the top 8 in 3, behind Engine B.
    table = rule_miner.Table({"rules": {
        "G:!E0 => E0 == 0": {"engine": "G", "examples": 5, "profile": {"-sltiu r,r,1": 1}},
        "A:swap:adjacent": {"engine": "A", "examples": 1, "profile": {"-sltiu r,r,1": 1, "-x": 1}}}})
    monkeypatch.setattr(rule_miner, "features", lambda diff: collections.Counter({"-sltiu r,r,1": 1, "-x": 1}))
    src = "s32 k(s32 x, s32 y) {\n    y = 1;\n    x = 2;\n    y = 3;\n    x = 4;\n    return x == 0;\n}\n"
    props = rule_miner.proposals(src, "k", "unused", table=table, limit=2)
    assert any(p[1] == "G:E0 == 0 => !E0" for p in props)


def test_the_generated_cap_counts_rules_that_apply(monkeypatch):
    # G3b (2026-09-30): the cap was taken over SCORED entries, so 24 high scorers that matched nothing hid the rule
    # that did. G then fired in 5 of 50 functions although one applied in 37.
    rules = {f"G:E0 + {n} => {n} + E0": {"examples": 1, "profile": {"-a": 1, "-b": 1}} for n in range(100, 140)}
    rules["G:!E0 => E0 == 0"] = {"examples": 1, "profile": {"-a": 1}}
    table = rule_miner.Table({"rules": rules})
    monkeypatch.setattr(rule_miner, "features", lambda diff: collections.Counter({"-a": 1, "-b": 1}))
    src = "s32 k(s32 x) {\n    return x == 0;\n}\n"
    assert any(p[1] == "G:E0 == 0 => !E0" for p in rule_miner.proposals(src, "k", "unused", table=table))
