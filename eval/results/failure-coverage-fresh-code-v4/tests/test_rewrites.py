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

def test_division_signedness_reuses_unsigned_cast_actuator():
    code = 's32 f(s32 x, s32 y) { return x / (u32)y; }'
    diff = _d([('div zero,t6,t1','divu zero,t6,t1')])
    rows = rewrites.signed_compare_rewrites(code,diff)
    assert any('x / (s32)y' in row(code) for row in rows)
    assert not rewrites.signed_compare_rewrites(code,diff.replace('divu zero,t6,t1','divu zero,t5,t1'))
    assert not rewrites.signed_compare_rewrites(code,_d([('divu zero,t6,t1','div zero,t6,t1')]))

def test_byte_pointer_stride_requires_target_literal_and_typed_local():
    code = 'void f(void) {\n    Player *p;\n    p += 0x60C;\n}\n'
    diff = '-addiu s0,s0,0x60c\n+lui s0,0x24\n'
    rows = rewrites.byte_pointer_step_rewrites(code, diff)
    assert len(rows) == 1
    assert 'p = (Player *)((unsigned char *)p + 0x60C);' in rows[0](code)
    assert rows[0](code+' ') == code+' '
    assert not rewrites.byte_pointer_step_rewrites(code, diff.replace('0x60c','0x60d'))
    assert not rewrites.byte_pointer_step_rewrites(code.replace('Player *p','int p'), diff)
    assert not rewrites.byte_pointer_step_rewrites(code.replace('p += 0x60C;', '// p += 0x60C;'), diff)
    assert not rewrites.byte_pointer_step_rewrites(code, diff.replace('s0,s0','sp,sp'))
    assert any(r.kind=='pointer-stride' for r in rewrites.propose(code,diff))


def test_small_byte_stride_is_not_excluded_by_large_record_heuristic():
    code='void f(void) {\n    s32 *p;\n    p += 4;\n}'
    rows=rewrites.byte_pointer_step_rewrites(code,'-addiu v0,v0,4\n+addiu v0,v0,16')
    assert len(rows)==1 and '(unsigned char *)p + 4' in rows[0](code)
    assert not rewrites.byte_pointer_step_rewrites(code,'-addiu v0,v0,0')

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


# ---------------------------------------------------- signed comparisons

def test_signed_compare_proposes_declaration_and_literal_repairs():
    code = ("typedef unsigned int u32;\n"
            "typedef signed int s32;\n"
            "struct S { u32 posX; u32 posY; };\n"
            "extern void place(int, u32, u32);\n"
            "void f(struct S *s) {\n"
            "    if (s->posX >= 0xFFCE0000) s->posX = 0xFFCE0000;\n"
            "}\n")
    diff = _d([("slt at,a1,v0", "sltu at,a1,v0")])
    rws = rewrites.signed_compare_rewrites(code, diff)
    outputs = [rw(code) for rw in rws]

    assert any("struct S { s32 posX; u32 posY; };" in out for out in outputs)
    assert any("-0x320000" in out for out in outputs)
    assert any("struct S { s32 posX; s32 posY; };" in out
               and "extern void place(int, s32, s32);" in out
               and out.count("-0x320000") == 2
               for out in outputs)
    assert all("typedef signed int s32;" in out for out in outputs)
    assert all("typedef unsigned int u32;" in out for out in outputs)


def test_signed_compare_requires_the_same_register_operands():
    code = "void f(void) { if (x < 0xFFFFFFFF) g(); }\n"
    diff = _d([("slt at,a1,v0", "sltu at,a2,v0")])
    assert rewrites.signed_compare_rewrites(code, diff) == []


def test_signed_compare_ignores_an_unrelated_opcode_change():
    code = "void f(void) { if (x < 0xFFFFFFFF) g(); }\n"
    diff = _d([("addu at,a1,v0", "subu at,a1,v0")])
    assert rewrites.signed_compare_rewrites(code, diff) == []


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


def test_argswap_never_edits_an_extern_prototype():
    code = ("extern void rotate(int phase, int x, int y);\n"
            "void f(void) {\n"
            "    rotate(3, obj->x, obj->y);\n"
            "}\n")
    diff = _d([("lh a2,0x26(s0)", "lh a1,0x26(s0)")])
    rws = rewrites.argswap_rewrites(code, diff)
    assert len(rws) == 2
    assert all("extern void rotate(int phase, int x, int y);" in rw(code)
               for rw in rws)


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


# ------------------------------------------------------------ stack frame

def _frame_diff(want="-0x48", got="-0x20"):
    return ("--- target\n+++ candidate\n@@ -1,4 +1,4 @@\n"
            f"-addiu sp,sp,{want}\n"
            f"+addiu sp,sp,{got}\n")


def test_frame_padding_restores_the_difference():
    """A shrunken frame is restored with an unused volatile local.

    `volatile` is what stops IDO optimising it away.
    """
    code = "void f(void)\n{\n    s32 a;\n}\n"
    rws = rewrites.frame_padding_rewrites(code, _frame_diff())
    assert rws and "0x28" in rws[0].label
    out = rws[0](code)
    assert "volatile" in out and "framePad[0x28]" in out


def test_frame_padding_declines_when_ours_is_already_larger():
    """Only a SHRUNKEN frame is repairable this way; padding cannot remove."""
    assert rewrites.frame_padding_rewrites(
        "void f(void)\n{\n}\n", _frame_diff("-0x20", "-0x48")) == []


def test_frame_padding_declines_without_a_frame_difference():
    assert rewrites.frame_padding_rewrites(
        "void f(void)\n{\n}\n", _frame_diff("-0x20", "-0x20")) == []


# ------------------------------------------------------ relocation symbols

def test_reloc_symbol_rewrite_changes_one_use_and_clones_the_extern():
    code = ("extern s16 gAssetHandles[];\n"
            "void f(void) {\n"
            "    a(gAssetHandles[10]);\n"
            "    b((u32)&gAssetHandles[0]);\n"
            "}\n")
    diff = _d([("lui t3,%hi(D_2003538)",
                "lui t1,%hi(gAssetHandles)")])
    rws = rewrites.reloc_symbol_rewrites(code, diff)
    assert len(rws) == 2

    changed = rws[-1](code)
    assert "extern s16 D_2003538[];" in changed
    assert "a(gAssetHandles[10])" in changed
    assert "b((u32)&D_2003538[0])" in changed


def test_reloc_symbol_rewrite_declines_without_a_type_to_clone():
    code = "void f(void) { b((u32)&gAssetHandles[0]); }\n"
    diff = _d([("lui t3,%hi(D_2003538)",
                "lui t1,%hi(gAssetHandles)")])
    assert rewrites.reloc_symbol_rewrites(code, diff) == []


def test_reloc_addend_difference_is_left_to_padding_repair():
    code = ("extern s16 gAssetHandles[];\n"
            "void f(void) { a(gAssetHandles[0]); }\n")
    diff = _d([("lui t3,%hi(gAssetHandles+8)",
                "lui t1,%hi(gAssetHandles)")])
    assert rewrites.reloc_symbol_rewrites(code, diff) == []


# ------------------------------------------------- statement order (alloc)

_ALLOC_DIFF = _d([("addu a2,v0,v1", "addu a3,v0,v1"),
                  ("lw t3,0x10(s0)", "lw t4,0x10(s0)")])


def test_statement_order_swaps_independent_neighbours():
    code = ("void f(void) {\n"
            "    blk->w0 = 1;\n"
            "    blk->w1 = 2;\n"
            "}\n")
    rws = rewrites.statement_order_rewrites(code, _ALLOC_DIFF)
    assert len(rws) == 1
    out = rws[0](code)
    assert out.index("blk->w1") < out.index("blk->w0")


def test_statement_order_declines_a_dependent_pair():
    """The second reads what the first writes, so the order is meaning."""
    code = ("void f(void) {\n"
            "    p = q + 8;\n"
            "    blk = (Blk *)p;\n"
            "}\n")
    assert rewrites.statement_order_rewrites(code, _ALLOC_DIFF) == []


def test_statement_order_declines_a_member_dependency():
    code = ("void f(void) {\n"
            "    blk->w0 = 1;\n"
            "    x = blk->w0;\n"
            "}\n")
    assert rewrites.statement_order_rewrites(code, _ALLOC_DIFF) == []


def test_statement_order_treats_a_cast_as_an_expression_not_a_call():
    """A bare `(` is not a call. Reading it as one refused every cast, and
    the search space for the function this was built for collapsed to two."""
    code = ("void f(void) {\n"
            "    gPtr = (u8 *)blk + 8;\n"
            "    blk->w0 = 1;\n"
            "}\n")
    assert rewrites.statement_order_rewrites(code, _ALLOC_DIFF)


def test_statement_order_declines_when_a_call_is_involved():
    code = ("void f(void) {\n"
            "    blk->w0 = 1;\n"
            "    blk->w1 = getBase(h);\n"
            "}\n")
    assert rewrites.statement_order_rewrites(code, _ALLOC_DIFF) == []


def test_statement_order_declines_a_residual_with_a_real_layout_fault():
    """A wrong struct offset has its own generator; this one must not fire."""
    layout = _d([("lw t3,0x10(s0)", "lw t3,0x14(s0)")])
    code = "void f(void) {\n    a->x = 1;\n    b->y = 2;\n}\n"
    assert rewrites.statement_order_rewrites(code, layout) == []


def test_statement_order_accepts_a_stack_slot_difference():
    """An sp-relative offset is a spill home the allocator chose, not a field."""
    diff = _d([("addu a2,v0,v1", "addu a3,v0,v1"),
               ("sw v1,0x18(sp)", "sw v1,0x1c(sp)")])
    code = "void f(void) {\n    a->x = 1;\n    b->y = 2;\n}\n"
    assert rewrites.statement_order_rewrites(code, diff)


def test_statement_order_gate_can_be_bypassed_mid_search():
    layout = _d([("lw t3,0x10(s0)", "lw t3,0x14(s0)")])
    code = "void f(void) {\n    a->x = 1;\n    b->y = 2;\n}\n"
    assert rewrites.statement_order_rewrites(code, layout, gate=False)


def test_statement_order_splits_compact_semantic_repair_line():
    """The exact compressRaceRecordReplayData actuation shape must fire.

    The semantic repairer kept three independent updates on one line.  The
    old whole-line assignment regex saw zero statements: splitting whitespace
    alone would still miss the two increments.
    """
    code = ("void f(void) {\n"
            "    if (literal) {\n"
            "        v1++; t5 = (u16 *)((char *)t5 + 2); v0++;\n"
            "    }\n"
            "}\n")
    runs = rewrites._independent_runs(code)
    assert any(len(run) == 3 for run in runs)

    outputs = [rw(code) for rw in
               rewrites.statement_order_rewrites(code, _ALLOC_DIFF)]
    assert outputs
    assert all(out.count("v1++;") == 1 for out in outputs)
    assert all(out.count("t5 = (u16 *)((char *)t5 + 2);") == 1
               for out in outputs)
    assert all(out.count("v0++;") == 1 for out in outputs)


def test_compact_statement_order_respects_data_dependency():
    code = ("void f(u8 *src) {\n"
            "    v0++; t5 = src[v0];\n"
            "}\n")
    assert rewrites._independent_runs(code) == []


def test_independent_windows_keep_safe_suffix_after_a_conflict():
    """A conflict with an early statement must not hide a later safe pair."""
    code = ("void f(u8 *src) {\n"
            "    t9 = src[v0]; v1++; t5 = t5 + 1; v0++;\n"
            "}\n")
    runs = rewrites._independent_runs(code)
    texts = [[statement.text.strip() for statement in run] for run in runs]
    assert ["v1++;", "t5 = t5 + 1;", "v0++;"] in texts


def test_compact_statement_order_stays_inside_inline_block():
    code = "void f(void) { if (ready) { t0 = t2; t1 = a3; } }\n"
    outputs = [rw(code) for rw in
               rewrites.statement_order_rewrites(code, _ALLOC_DIFF)]
    assert outputs
    assert all("if (ready) {" in out and out.rstrip().endswith("} }")
               for out in outputs)


def test_statement_order_splits_prologue_initializers_atomically():
    """Target store order must be reachable without producing illegal C89."""
    code = ("void f(s32 n, u16 *dst) {\n"
            "    s32 v0 = 0; /* input index */\n"
            "    s32 v1 = 1;\n"
            "    u16 *t5;\n"
            "\n"
            "    /* output header */\n"
            "    *dst = (u16)n;\n"
            "    t5 = dst + 1;\n"
            "}\n")
    split = rewrites._split_prologue_initializers(code)
    assert "s32 v0; /* input index */" in split
    assert "s32 v1;" in split
    assert split.index("u16 *t5;") < split.index("v0 = 0;")
    assert split.index("v0 = 0;") < split.index("v1 = 1;")
    assert split.index("v1 = 1;") < split.index("*dst = (u16)n;")

    proposals = rewrites.statement_order_rewrites(code, _ALLOC_DIFF)
    assert proposals
    assert any("split prologue initializers" in proposal.label
               for proposal in proposals)


def test_prologue_initializer_split_declines_const_and_arrays():
    const_code = ("void f(void) {\n"
                  "    const s32 limit = 4;\n"
                  "    use(limit);\n"
                  "}\n")
    array_code = ("void f(void) {\n"
                  "    s32 values[2] = { 1, 2 };\n"
                  "    use(values);\n"
                  "}\n")
    assert rewrites._split_prologue_initializers(const_code) == const_code
    assert rewrites._split_prologue_initializers(array_code) == array_code


def test_pointer_store_is_a_reorderable_statement():
    code = ("void f(s32 n, u16 *dst) {\n"
            "    v0 = 0;\n"
            "    /* comments do not change adjacency */\n"
            "    *dst = (u16)n;\n"
            "}\n")
    runs = rewrites._independent_runs(code)
    assert any([statement.lhs for statement in run] == ["v0", "*dst"]
               for run in runs)


def test_statement_order_budget_samples_later_independent_runs():
    """A large prologue must not hide a small lever near the residual."""
    code = ("void f(void) {\n"
            "    a = 0;\n"
            "    b = 0;\n"
            "    c = 0;\n"
            "    d = 0;\n"
            "    consume();\n"
            "    x++; y++;\n"
            "}\n")

    proposals = rewrites.statement_order_rewrites(
        code, _ALLOC_DIFF, max_variants=2)
    outputs = [proposal(code) for proposal in proposals]

    assert len(proposals) == 2
    assert any(output.index("y++") < output.index("x++")
               for output in outputs)


def test_no_generator_ever_emits_a_do_token():
    """build.sh rejects the token outright, so an emitted `do` is a wasted
    compile at best. Asserted over every generator, not just the loop one."""
    import re as _re
    code = ("void f(void) {\n"
            "    blk->w0 = 1;\n"
            "    blk->w1 = 2;\n"
            "    for (i = 0; i < 4; i++) g();\n"
            "}\n")
    for rw in rewrites.propose(code, _ALLOC_DIFF):
        assert not _re.search(r"\bdo\b", rw(code))


# ------------------------------------------------ comparison operand order

_CMP_DIFF = _d([("slt at,v1,v0", "slt at,v0,v1")])


def test_comparison_swap_mirrors_the_operator():
    """`a < b` and `b > a` are the same predicate; `b < a` is not."""
    code = "void f(void) {\n    if (alpha < beta) { g(); }\n}\n"
    rws = rewrites.compare_swap_rewrites(code, _CMP_DIFF)
    assert rws
    out = rws[0](code)
    assert "beta > alpha" in out


def test_comparison_swap_handles_equality_and_members():
    code = "void f(void) {\n    if (p->a != q->b) { g(); }\n}\n"
    rws = rewrites.compare_swap_rewrites(code, _d([("bne a0,v0,60",
                                                    "bne v0,a0,60")]))
    assert rws
    assert "q->b != p->a" in rws[0](code)


def test_comparison_swap_declines_without_a_swapped_residual():
    code = "void f(void) {\n    if (alpha < beta) { g(); }\n}\n"
    same = _d([("slt at,v1,v0", "slt at,v1,v0")])
    assert rewrites.compare_swap_rewrites(code, same) == []


def test_comparison_swap_declines_on_a_plain_register_difference():
    """Different registers entirely is allocation, not operand order."""
    code = "void f(void) {\n    if (alpha < beta) { g(); }\n}\n"
    other = _d([("slt at,v1,v0", "slt at,t3,t4")])
    assert rewrites.compare_swap_rewrites(code, other) == []


def test_branch_sentinels_materialize_swapped_literal_operands():
    code = ("void f(struct Entry *entry) {\n"
            "    s16 status;\n"
            "    for (;;) {\n"
            "        status = entry->status;\n"
            "        if (status != -2) {\n"
            "            if (status != -1) return 1;\n"
            "            entry++;\n"
            "            continue;\n"
            "        }\n"
            "        return 0;\n"
            "    }\n"
            "}\n")
    diff = _d([("beq a0,v1,48", "beq v1,a0,48"),
               ("beq a1,v1,40", "beq v1,a1,40")])
    outputs = [rw(code) for rw in rewrites.branch_sentinel_rewrites(code, diff)]
    assert outputs
    assert all("if (status != -2)" not in out for out in outputs)
    assert all(re.search(r"if \(branchSentinel\d+ != status\)", out)
               for out in outputs)
    assert all(out.index("branchSentinel0 =") < out.index("for (;;)")
               for out in outputs)


def test_branch_sentinels_require_an_exact_register_swap():
    code = "void f(void) { if (status != -2) return; }\n"
    diff = _d([("beq a0,v1,48", "beq v1,a2,48")])
    assert rewrites.branch_sentinel_rewrites(code, diff) == []


def test_pointer_table_rewrite_removes_slot_address_of():
    code = ("extern struct Entry *gTable[];\n"
            "void f(void) { entry = &gTable[index]; }\n")
    diff = _d([("lw v0,%lo(gTable)(v0)",
                "addiu t8,t8,%lo(gTable)")])
    rws = rewrites.pointer_table_deref_rewrites(code, diff)
    assert rws
    assert "entry = gTable[index]" in rws[0](code)


def test_pointer_table_rewrite_requires_the_same_candidate_symbol():
    code = "void f(void) { entry = &gTable[index]; }\n"
    diff = _d([("lw v0,%lo(gTable)(v0)",
                "addiu t8,t8,%lo(gOther)")])
    assert rewrites.pointer_table_deref_rewrites(code, diff) == []


# ------------------------------------------------- relocation addend repair

_ADDEND = _d([("lbu t7,%lo(gRacePlayers+8)(t7)",
               "lbu t7,%lo(gRacePlayers)(t7)")])


def test_addend_repair_finds_an_inline_anonymous_struct():
    code = ("struct {\n    u8 menuState;\n    u8 other;\n} gRacePlayers[4];\n")
    rws = rewrites.reloc_padding_rewrites(code, _ADDEND)
    assert rws and "rpad00[0x8]" in rws[0](code)


def test_addend_repair_follows_a_named_typedef():
    """The ordinary shape of a decompiled header, and it used to decline."""
    code = ("typedef struct {\n    u8 menuState;\n    u8 other;\n"
            "} RacePlayer;\n"
            "extern RacePlayer gRacePlayers[4];\n")
    rws = rewrites.reloc_padding_rewrites(code, _ADDEND)
    assert rws, "a typedef'd element type must still be reachable"
    assert "rpad00[0x8]" in rws[0](code)


def test_addend_repair_declines_for_an_unknown_symbol():
    code = "typedef struct {\n    u8 a;\n} T;\n"
    assert rewrites.reloc_padding_rewrites(code, _ADDEND) == []


def test_allocation_gate_tolerates_a_minority_of_layout_faults():
    """Demanding purity gave the tier's most allocation-shaped function --
    41 register faults, zero structural -- no proposals at all, because two
    offsets were also wrong and no layout generator covered those two."""
    pairs = [("addu a2,v0,v%d" % i, "addu a3,v0,v%d" % i) for i in range(6)]
    pairs.append(("lw t3,0x10(s0)", "lw t3,0x14(s0)"))
    assert rewrites._allocation_shaped(_d(pairs))


def test_allocation_gate_still_refuses_a_layout_dominated_residual():
    pairs = [("lw t%d,0x10(s0)" % i, "lw t%d,0x14(s0)" % i) for i in range(6)]
    pairs.append(("addu a2,v0,v1", "addu a3,v0,v1"))
    assert not rewrites._allocation_shaped(_d(pairs))


# ------------------------------------------------------ inlining a temporary

_ALLOC6 = _d([("addu a2,v0,v%d" % i, "addu a3,v0,v%d" % i) for i in range(4)])


def test_inline_temporary_removes_a_single_use_local():
    code = ("void f(void) {\n"
            "    s32 tmp = actor->speed;\n"
            "    g(tmp);\n"
            "}\n")
    rws = rewrites.inline_temporary_rewrites(code, _ALLOC6)
    assert rws
    out = rws[0](code)
    assert "s32 tmp" not in out and "g((actor->speed))" in out


def test_preincrement_lookup_collapses_unsigned_narrow_temporary():
    code = ("typedef struct { u8 randomIndex; } State;\n"
            "u8 f(State *state) {\n"
            "    u8 next = state->randomIndex + 1;\n"
            "    state->randomIndex = next;\n"
            "    return table[next & 0xFF];\n"
            "}\n")
    proposals = rewrites.preincrement_lookup_rewrites(code, _ALLOC6)
    assert proposals
    assert "return table[++state->randomIndex];" in proposals[0](code)


def test_preincrement_lookup_requires_matching_unsigned_field_width():
    code = ("typedef struct { s8 randomIndex; } State;\n"
            "u8 f(State *state) {\n"
            "    u8 next = state->randomIndex + 1;\n"
            "    state->randomIndex = next;\n"
            "    return table[next & 0xFF];\n"
            "}\n")
    assert rewrites.preincrement_lookup_rewrites(code, _ALLOC6) == []


def test_narrow_increment_enumerates_promoted_temporary_types():
    code = ("typedef struct { u8 randomIndex; } State;\n"
            "u8 f(State *state) {\n"
            "    u8 next = state->randomIndex + 1;\n"
            "    state->randomIndex = next;\n"
            "    return table[next & 0xFF];\n"
            "}\n")
    proposals = rewrites.narrow_increment_type_rewrites(code, _ALLOC6)
    outputs = [proposal(code) for proposal in proposals]
    assert any("s32 next" in output for output in outputs)
    assert any("u32 next" in output for output in outputs)


def test_narrow_increment_type_declines_signed_field():
    code = ("typedef struct { s8 randomIndex; } State;\n"
            "u8 f(State *state) {\n"
            "    u8 next = state->randomIndex + 1;\n"
            "    state->randomIndex = next;\n"
            "    return table[next & 0xFF];\n"
            "}\n")
    assert rewrites.narrow_increment_type_rewrites(code, _ALLOC6) == []


def test_materialize_increment_input_creates_distinct_unsigned_web():
    code = ("typedef struct { u8 randomIndex; } State;\n"
            "u8 f(State *state) {\n"
            "    s16 next = state->randomIndex + 1;\n"
            "    state->randomIndex = next;\n"
            "    return table[next & 0xFF];\n"
            "}\n")
    proposals = rewrites.materialize_increment_input_rewrites(code, _ALLOC6)
    assert proposals
    output = proposals[0](code)
    assert "u8 _increment_input = state->randomIndex;" in output
    assert "s16 next = _increment_input + 1;" in output


def test_inline_temporary_declines_a_local_read_twice():
    code = ("void f(void) {\n"
            "    s32 tmp = actor->speed;\n"
            "    g(tmp);\n"
            "    h(tmp);\n"
            "}\n")
    assert rewrites.inline_temporary_rewrites(code, _ALLOC6) == []


def test_inline_temporary_declines_when_the_initialiser_calls():
    """A call's side effects would move with the expression."""
    code = ("void f(void) {\n"
            "    s32 tmp = getSpeed(actor);\n"
            "    g(tmp);\n"
            "}\n")
    assert rewrites.inline_temporary_rewrites(code, _ALLOC6) == []


def test_inline_temporary_declines_when_an_input_changes_first():
    code = ("void f(void) {\n"
            "    s32 tmp = actor->speed;\n"
            "    actor = next;\n"
            "    g(tmp);\n"
            "}\n")
    assert rewrites.inline_temporary_rewrites(code, _ALLOC6) == []


def test_statement_relocation_reaches_beyond_adjacent_swaps():
    """Adjacent swaps gave eight candidates and stalled; moving a statement
    to any position in its independent run reaches orderings that would take
    a long chain of swaps to find."""
    code = ("void f(void) {\n"
            "    p->w0 = 1;\n"
            "    p->w1 = 2;\n"
            "    p->w2 = 3;\n"
            "}\n")
    rws = rewrites.statement_order_rewrites(code, _ALLOC_DIFF)
    outs = {r(code) for r in rws}
    assert len(outs) >= 3, "three independent statements permit more than a swap"
    assert all("p->w0" in o and "p->w1" in o and "p->w2" in o for o in outs)


def test_independent_runs_stop_at_a_dependency():
    code = ("void f(void) {\n"
            "    p->w0 = 1;\n"
            "    p->w1 = 2;\n"
            "    p = next;\n"
            "    q->w0 = 3;\n"
            "}\n")
    runs = rewrites._independent_runs(code)
    assert runs, "the first two statements form a run"
    assert all(len(r) <= 2 for r in runs), "the reassignment must break the run"


def test_allocation_gate_fires_when_one_opcode_is_substituted():
    """A substitution is not a missing web, and must not close the gate.

    compressRaceRecordReplayData's residual at 94.130: eighteen against
    eighteen instructions, seventeen pairs differing only in registers, zero
    layout faults, and one `bgtz`/`bnez` pair. Strict opcode-multiset equality
    refused it, statement_order_rewrites gated itself off, and propose()
    returned zero proposals of any kind on the purest allocation residual in
    the tier. Asserts the gate FIRES, per CLAUDE.md.
    """
    diff = "\n".join([
        "--- target", "+++ candidate", "@@ -1,6 +1,6 @@",
        "-addiu t5,a2,2", "-move t3,zero", "-addu t4,s0,v0", "-bgtz t1,c4",
        "+addiu t3,a2,2", "+move t4,zero", "+addu t5,s0,v0", "+bnez t1,c4",
    ])
    assert rewrites._allocation_shaped(diff)


def test_allocation_gate_accepts_register_erased_local_order_change():
    diff = "\n".join([
        "--- target", "+++ candidate", "@@ -1,2 +1,2 @@",
        "-addiu t3,t3,1", "-bne t6,t7,7c",
        "+bne t6,t7,7c", "+addiu t5,t5,1",
    ])

    assert rewrites._allocation_shaped(diff)


def test_allocation_gate_still_declines_on_a_missing_instruction():
    """The original worry survives: a surplus/missing instruction is not allocation."""
    diff = "\n".join([
        "--- target", "+++ candidate", "@@ -1,4 +1,3 @@",
        "-addiu t5,a2,2", "-move t3,zero", "-lbu t6,0(t4)",
        "+addiu t3,a2,2", "+move t4,zero",
    ])
    assert not rewrites._allocation_shaped(diff)
