"""Tests for residual-derived rewrite generators.

These propose source edits, so the tests are mostly about refusing to propose
when the residual does not actually say what the rewrite would assert.
"""

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
