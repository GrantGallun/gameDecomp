"""Generic two-way rewrites for rule mining (solver.rewrite_library)."""
from solver import rewrite_library as rl

F = """s32 f(s32 *p, s32 n, s32 c) {
    s32 i;
    s32 x;
    s32 y;

    x = 0;
    y = c;
    for (i = 0; i < n; i++) {
        x += p[i];
    }
    if (c != 0) {
        y = 1;
    } else {
        y = 2;
    }
    x = x + 3;
    y++;
    while (x < n) {
        x = x * 2;
    }
    x = c ? y : 7;
    return x + y;
}
"""


def rules(fn):
    return {r for r, _l, _s in fn(F, "f")}


def test_each_rewrite_fires_on_its_shape():
    assert "inc:pp->pe" in rules(rl.increment_forms) and "inc:pp->ae" in rules(rl.increment_forms)
    assert "compound:long->short" in rules(rl.compound_assign) and "compound:short->long" in rules(rl.compound_assign)
    assert "loop:for->rotated" in rules(rl.counted_loop_rotation)
    assert "temp:direct->copyback" in rules(rl.copyback_temporary)
    assert "ifswap:plain->negated" in rules(rl.if_arm_swap)
    assert "truth:explicit->implicit" in rules(rl.truth_test)
    assert {"ternary:ternary->if", "ternary:if->ternary"} <= rules(rl.ternary_if)
    assert "index:subscript->pointer" in rules(rl.index_pointer)
    assert "while:while->ifdo" in rules(rl.while_rotation)


def test_for_rotation_round_trips_through_counted_loop():
    rotated = [s for r, _l, s in rl.counted_loop_rotation(F, "f") if r == "loop:for->rotated"][0]
    assert "for (;;)" in rotated and "if (n > 0)" in rotated and "i += 1;" in rotated
    back = [s for r, _l, s in rl.counted_loop_rotation(rotated, "f") if r == "loop:rotated->for"]
    assert back and "for (i = 0; i < n; i++) {" in back[0]


def test_if_swap_negates_and_swaps():
    new = rl.if_arm_swap(F, "f")[0][2]
    assert "if (!(c != 0)) {\n        y = 2;\n    } else {\n        y = 1;\n    }" in new


def test_copyback_declares_a_typed_temporary():
    new = [s for r, _l, s in rl.copyback_temporary(F, "f") if r == "temp:direct->copyback"][0]
    assert "s32 temp_rw0;" in new and "temp_rw0 = 0;\n    x = temp_rw0;" in new


def test_rotation_declines_with_continue_or_side_effect_conditions():
    src = F.replace("x += p[i];", "if (p[i]) continue;\n        x += 1;")
    assert "loop:for->rotated" not in {r for r, _l, _s in rl.counted_loop_rotation(src, "f")}
    src2 = F.replace("while (x < n) {", "while (g(x) < n) {")
    assert not [r for r, _l, _s in rl.while_rotation(src2, "f") if r == "while:while->ifdo"]


def test_statement_swap_needs_independence():
    src = """void g(s32 a, s32 b) {
    s32 x;
    s32 y;

    x = a + 1;
    y = b + 2;
    x = y;
}
"""
    swaps = rl.statement_swap(src, "g")
    assert len(swaps) == 1 and "y = b + 2;\n    x = a + 1;" in swaps[0][2]


def test_all_variants_never_raises_and_changes_source():
    out = rl.all_variants(F, "f")
    assert out and all(s != F for _r, _l, s in out)


def test_truth_and_index_rewrites_keep_precedence():
    src = """s32 h(s32 a, S *p, s32 i) {
    if (a & 4) {
        return p[i].x;
    }
    return 0;
}
"""
    truth = [s for r, _l, s in rl.truth_test(src, "h") if r == "truth:implicit->explicit"][0]
    assert "if ((a & 4) != 0)" in truth
    idx = [s for r, _l, s in rl.index_pointer(src, "h") if r == "index:subscript->pointer"][0]
    assert "(*(p + i)).x" in idx
