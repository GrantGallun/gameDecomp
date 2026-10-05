"""Mined rule table, features, matcher and Engine B template application (solver.rule_miner)."""
import collections

from patterns import equivalences
from solver import rule_miner


def _diff(target, candidate):
    import difflib
    lines = ["--- t", "+++ c", f"@@ -1,{len(target)} +1,{len(candidate)} @@"]
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, target, candidate, autojunk=False).get_opcodes():
        if tag == "equal":
            lines += [" " + x for x in target[i1:i2]]
        else:
            lines += ["-" + x for x in target[i1:i2]] + ["+" + x for x in candidate[j1:j2]]
    return "\n".join(lines)


def test_abstract_keeps_meaningful_constants_and_blanks_the_rest():
    assert rule_miner.abstract("andi t3,t2,0xff") == "andi r,r,0xff"
    assert rule_miner.abstract("lw t6,0x24(sp)") == "lw r,N(sp)"
    assert rule_miner.abstract("lui a0,%hi(_motorstartbuf)") == "lui r,%hi(S)"
    assert rule_miner.abstract("bnez t0,40") == "bnez r,L"


def test_features_are_the_one_sided_abstract_instructions_plus_classes():
    t = ["addiu sp,sp,-0x20", "lw t7,0x1c(sp)", "addiu t8,t7,1", "jr ra", "nop"]
    c = ["addiu sp,sp,-0x20", "lw t7,0x1c(sp)", "addiu t8,t7,1", "sw t8,0x18(sp)", "lw t1,0x18(sp)", "jr ra", "nop"]
    f = rule_miner.features(_diff(t, c))
    assert f["+sw r,N(sp)"] == 1 and f["+lw r,N(sp)"] == 1 and "-lw r,N(sp)" not in f
    assert any(k.startswith("class:") for k in f)


def test_inverse_swaps_direction():
    assert rule_miner.inverse("loop:for->rotated") == "loop:rotated->for"
    assert rule_miner.inverse("swap:adjacent") == "swap:adjacent"


def test_template_applies_with_consistent_placeholders():
    parent = "void f(s32 a, s32 b) {\n    a = a + 1;\n    b = b + 1;\n}\n"
    child = "void f(s32 a, s32 b) {\n    a += 1;\n    b = b + 1;\n}\n"
    hunks = equivalences.directed_hunks(parent, child)
    src = "void g(s32 q) {\n    q = q + 1;\n}\n"
    begin, stop = src.index("{") + 1, src.rindex("}")
    out = rule_miner.apply_template(src, begin, stop, hunks)
    assert out and "q += 1" in out[0].replace(" ", " ")
    mismatch = "void g(s32 q, s32 r) {\n    q = r + 1;\n}\n"            # I0 = I0 + 1 must not bind two names
    b2, s2 = mismatch.index("{") + 1, mismatch.rindex("}")
    assert rule_miner.apply_template(mismatch, b2, s2, hunks) == []


def test_score_prefers_the_rule_whose_profile_matches_and_ignores_pruned():
    table = rule_miner.Table({"rules": {
        "A:loop:for->rotated": {"examples": 4, "profile": {"+sw r,N(sp)": 4, "class:registers": 4}},
        "A:inc:pe->pp": {"examples": 10, "profile": {"class:registers": 10}},
        "A:dead": {"examples": 5, "profile": {"+sw r,N(sp)": 5}, "pruned": True}}})
    residual = collections.Counter({"+sw r,N(sp)": 1, "class:registers": 1})
    assert table.score("A:loop:for->rotated", residual) > table.score("A:inc:pe->pp", residual)
    assert table.score("A:dead", residual) == 0.0


def test_localisation_prefers_the_candidate_on_residual_lines():
    src = "void f(s32 a, s32 b) {\n    a = 1;\n    b = 2;\n}\n"
    on_line_3 = src.replace("b = 2;", "b = 3;")
    on_line_2 = src.replace("a = 1;", "a = 5;")
    scored = [(1.0, "A:x", "edit line 2", on_line_2), (1.0, "A:y", "edit line 3", on_line_3)]
    ranked = sorted(rule_miner._localised(scored, src, {3: 4}), key=lambda x: -x[0])
    assert ranked[0][1] == "A:y" and ranked[0][0] > ranked[1][0]
    assert rule_miner._localised(scored, src, None) == scored


def test_all_variants_limit_raises_the_site_cap():
    from solver import rewrite_library
    src = "void f(s32 *p) {\n" + "".join(f"    p[{k}] = {k};\n" for k in range(8)) + "}\n"
    few = [r for r, _l, _s in rewrite_library.all_variants(src, "f") if r == "index:subscript->pointer"]
    many = [r for r, _l, _s in rewrite_library.all_variants(src, "f", limit=12) if r == "index:subscript->pointer"]
    assert len(few) == 3 and len(many) == 8 and rewrite_library.LIMIT == 3
