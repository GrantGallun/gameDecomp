"""Tests for residual-derived rewrite generators.

These propose source edits, so the tests are mostly about refusing to propose
when the residual does not actually say what the rewrite would assert.
"""

import re

from solver import rewrites


def _d(pairs):
    out = ["--- target", "+++ candidate", "@@ -1,9 +1,9 @@"]
    for a, b in pairs:
        out.append("-" + a)
        out.append("+" + b)
    return "\n".join(out)


# ------------------------------------------------------------- immediates

def test_immediate_rewrite_from_a_constant_difference():
    code = "void f(void) {\n    for (i = 0; i < 4; i++) g();\n}\n"
    rws = rewrites.immediate_rewrites(code, _d([("slti at,v1,5", "slti at,v1,4")]))
    assert rws, "a differing constant should propose a rewrite"
    assert "i < 5" in rws[0](code)


def test_immediate_rewrite_declines_when_the_literal_is_ambiguous():
    """Two occurrences means rewriting an arbitrary one is a coin flip."""
    code = "void f(void) {\n    a = 4;\n    b = 4;\n}\n"
    assert rewrites.immediate_rewrites(
        code, _d([("slti at,v1,5", "slti at,v1,4")])) == []


def test_immediate_rewrite_ignores_register_differences():
    code = "void f(void) { a = 4; }\n"
    assert rewrites.immediate_rewrites(
        code, _d([("addu v0,v1,a0", "addu v0,v1,a1")])) == []


# ---------------------------------------------------------------- argswap

def test_argswap_proposed_when_registers_are_exchanged():
    code = "void f(void) {\n    setRot(3, obj->rotY, obj->field24, 0);\n}\n"
    rws = rewrites.argswap_rewrites(code, _d([
        ("lh a2,0x26(s0)", "lh a1,0x26(s0)"),
        ("lh a1,0x24(s0)", "lh a2,0x24(s0)"),
    ]))
    assert any("swap args 1/2" in r.label for r in rws)


def test_argswap_not_proposed_without_a_register_exchange():
    code = "void f(void) {\n    setRot(3, a, b, 0);\n}\n"
    assert rewrites.argswap_rewrites(
        code, _d([("lh a1,0x26(s0)", "lh a1,0x24(s0)")])) == []


# ------------------------------------------------- relocation addend padding

def test_reloc_padding_from_an_addend_difference():
    """%lo(sym+8) vs %lo(sym) says the field sits 8 bytes further in."""
    code = ("struct RacePlayer {\n  u8 menuState;\n} gRacePlayers[8];\n"
            "void f(void) { g(); }\n")
    rws = rewrites.reloc_padding_rewrites(code, _d([
        ("lbu t7,%lo(gRacePlayers+8)(t7)", "lbu t7,%lo(gRacePlayers)(t7)"),
    ]))
    assert rws, "an addend difference should propose padding"
    out = rws[0](code)
    assert "rpad00[0x8]" in out
    assert out.index("rpad00") < out.index("menuState")


def test_reloc_padding_declines_on_a_different_symbol():
    code = "struct S {\n  u8 a;\n} gOther[4];\n"
    assert rewrites.reloc_padding_rewrites(code, _d([
        ("lbu t7,%lo(gRacePlayers+8)(t7)", "lbu t7,%lo(gOther)(t7)"),
    ])) == []


def test_reloc_padding_never_removes_bytes():
    """A negative delta would mean deleting padding, which this cannot do."""
    code = "struct S {\n  char pad[8];\n  u8 a;\n} gSym[4];\n"
    assert rewrites.reloc_padding_rewrites(code, _d([
        ("lbu t7,%lo(gSym)(t7)", "lbu t7,%lo(gSym+8)(t7)"),
    ])) == []


def test_propose_returns_every_kind():
    code = ("struct RacePlayer {\n  u8 menuState;\n} gRacePlayers[8];\n"
            "void f(void) {\n    for (i = 0; i < 4; i++) setRot(3, a, b, c);\n}\n")
    diff = _d([("lbu t7,%lo(gRacePlayers+8)(t7)", "lbu t7,%lo(gRacePlayers)(t7)"),
               ("slti at,v1,5", "slti at,v1,4")])
    kinds = {r.kind for r in rewrites.propose(code, diff)}
    assert "layout" in kinds and "immediate" in kinds


# ---------------------------------------------------- redundant mask removal

def test_drop_mask_proposed_when_we_emit_an_extra_andi():
    """`+andi v0,t6,0xffff` with no counterpart is a mask the target lacks.

    On requestRumbleMotorStart one surplus `& 0xFFFF` displaced every later
    branch, so an 18-instruction function showed three structural faults.
    """
    code = "void f(void) {\n    a = (x & 0xFFFF) << 1;\n}\n"
    rws = rewrites.drop_mask_rewrites(
        code, _d([("sllv t0,t9,t6", "andi v0,t6,0xffff")]))
    assert rws
    assert "& 0xFFFF" not in rws[0](code)


def test_drop_mask_not_proposed_without_a_surplus_andi():
    code = "void f(void) {\n    a = (x & 0xFFFF) << 1;\n}\n"
    assert rewrites.drop_mask_rewrites(
        code, _d([("addu v0,v1,a0", "addu v0,v1,a1")])) == []


def test_drop_mask_ignores_a_narrow_andi_that_is_not_a_width_mask():
    """0x7 is a real bit test, not a zero-extension no-op."""
    code = "void f(void) {\n    a = (x & 0xFFFF) << 1;\n}\n"
    assert rewrites.drop_mask_rewrites(
        code, _d([("sllv t0,t9,t6", "andi v0,t6,0x7")])) == []


def test_drop_mask_only_touches_true_width_masks():
    """0xFFF is a 12-bit mask, not a zero-extension no-op.

    The safety argument for this rewrite is that lhu/lbu already zero-extend,
    which covers 0xFF and 0xFFFF and nothing else. A looser pattern proposed
    `drop mask 0xFFF` edits that changed meaning and still scored better.
    """
    diff = _d([("sllv t0,t9,t6", "andi v0,t6,0xffff")])
    assert rewrites.drop_mask_rewrites("a = x & 0xFFF;\n", diff) == []
    assert rewrites.drop_mask_rewrites("a = x & 0xFFFFF;\n", diff) == []
    assert rewrites.drop_mask_rewrites("a = x & 0xFF;\n", diff)
    assert rewrites.drop_mask_rewrites("a = x & 0xFFFF;\n", diff)


# ------------------------------------------------------- loop shape

def _loop_diff():
    """A surplus conditional branch on our side: a top-tested entry guard."""
    return ("--- target\n+++ candidate\n@@ -1,9 +1,9 @@\n"
            "-addiu v1,v1,8\n"
            "-bne v1,a0,14\n"
            "+beq v1,v0,30\n"
            "+addiu v0,v0,8\n"
            "+bne v0,v1,18\n")


def test_loop_rewrite_never_emits_a_do_token():
    """build.sh rejects a bare `do` outright.

    The sanctioned form is for(;;) + break, which compiles to the same
    entry-guard-free shape. A first version of this generator emitted
    do/while and was rejected by the build in one compile.
    """
    code = "void f(void) {\n    while (p != end)\n    {\n        p += 4;\n    }\n}\n"
    rws = rewrites.loop_shape_rewrites(code, _loop_diff())
    assert rws, "a surplus guard should propose a bottom-test rewrite"
    out = rws[0](code)
    assert not re.search(r"\bdo\b", out), "must never emit a `do` token"
    assert "for (;;)" in out and "break;" in out


def test_loop_rewrite_declines_on_an_own_level_continue():
    """`continue` in a bottom-tested loop jumps to the condition test; in
    for(;;) it skips the trailing break and the loop never terminates. That is
    a hang, not a low score, so it must be refused rather than measured."""
    code = ("void f(void) {\n    while (p != end)\n    {\n"
            "        if (x) continue;\n        p += 4;\n    }\n}\n")
    assert rewrites.loop_shape_rewrites(code, _loop_diff()) == []


def test_loop_rewrite_allows_a_continue_belonging_to_a_nested_loop():
    code = ("void f(void) {\n    while (p != end)\n    {\n"
            "        for (i = 0; i < 4; i++) { if (x) continue; }\n"
            "        p += 4;\n    }\n}\n")
    assert rewrites.loop_shape_rewrites(code, _loop_diff())


def test_loop_rewrite_needs_a_surplus_branch():
    """Equal branch counts mean no entry guard to remove."""
    code = "void f(void) {\n    while (p != end)\n    {\n        p += 4;\n    }\n}\n"
    even = ("--- target\n+++ candidate\n@@ -1,4 +1,4 @@\n"
            "-bne v1,a0,14\n+bne v0,v1,18\n")
    assert rewrites.loop_shape_rewrites(code, even) == []
