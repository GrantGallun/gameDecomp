"""Class-by-class ordering of residuals (solver.residual_classes)."""
from types import SimpleNamespace

from solver import residual_classes, site_edits


def _diff(target, candidate):
    """Unified-diff shape the oracle emits: '-' target, '+' candidate, ' ' both."""
    import difflib
    lines = ["--- target", "+++ candidate", f"@@ -1,{len(target)} +1,{len(candidate)} @@"]
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, target, candidate, autojunk=False).get_opcodes():
        if tag == "equal":
            lines += [" " + x for x in target[i1:i2]]
        else:
            lines += ["-" + x for x in target[i1:i2]] + ["+" + x for x in candidate[j1:j2]]
    return "\n".join(lines)


def _att(target, candidate):
    return SimpleNamespace(compiled=True, exact=False, diff=_diff(target, candidate), score=50.0)


TARGET = ["lw v0,0x10(a0)", "addiu v0,v0,1", "sw v0,0x10(a0)", "lw t6,0x14(a0)", "jr ra", "nop"]


def test_width_counts_one_sided_extensions():
    cand = ["lw v0,0x10(a0)", "addiu v0,v0,1", "sll v0,v0,16", "sra v0,v0,16", "sw v0,0x10(a0)",
            "lw t6,0x14(a0)", "jr ra", "nop"]
    assert residual_classes.counts(_diff(TARGET, cand))["width"] == 1     # one sll/sra pair = one extension


def test_control_flow_counts_branch_inventory_not_addresses():
    t = ["beqz a0,10", "nop", "b 18", "nop", "jr ra", "nop"]
    shifted = ["addiu a0,a0,1", "beqz a0,14", "nop", "b 1c", "nop", "jr ra", "nop"]
    assert residual_classes.counts(_diff(t, shifted))["control_flow"] == 0
    inverted = ["bnez a0,10", "nop", "b 18", "nop", "jr ra", "nop"]
    assert residual_classes.counts(_diff(t, inverted))["control_flow"] == 2


def test_class_key_keeps_the_width_fix_the_gradient_discarded():
    # The motivating residual (width-edits-20260929): a retype removes the extension pair but the
    # rewritten expression shifts other instructions. `gradient` ranks it worse than its parent and the
    # search discarded it in 56 of 106 functions; the class key must keep it.
    parent = _att(TARGET, ["lw v0,0x10(a0)", "addiu v0,v0,1", "sll v0,v0,16", "sra v0,v0,16",
                           "sw v0,0x10(a0)", "lw t6,0x14(a0)", "jr ra", "nop"])
    child = _att(TARGET, ["lw v1,0x10(a0)", "addiu v1,v1,1", "move v0,v1", "sw v1,0x10(a0)",
                          "lw t7,0x14(a0)", "addu t7,t7,zero", "move v0,t7", "jr ra", "nop"])
    assert residual_classes.counts(child.diff)["width"] == 0
    assert site_edits.gradient(child) > site_edits.gradient(parent)          # the old rule's verdict
    assert residual_classes.key(child) < residual_classes.key(parent)        # class by class


def test_an_earlier_class_never_regresses_for_a_later_one():
    parent = _att(TARGET, ["lw v0,0x10(a0)", "addiu v0,v0,1", "sw v0,0x10(a0)", "lw t6,0x18(a0)",
                           "jr ra", "nop"])                                   # layout fault only
    child = _att(TARGET, ["lw v0,0x10(a0)", "addiu v0,v0,1", "sll v0,v0,16", "sra v0,v0,16",
                          "sw v0,0x10(a0)", "lw t6,0x14(a0)", "jr ra", "nop"])  # layout fixed, width broken
    assert residual_classes.key(child) > residual_classes.key(parent)


def test_uncompiled_last_and_exact_first():
    bad = SimpleNamespace(compiled=False, exact=False, diff="", score=0.0)
    done = SimpleNamespace(compiled=True, exact=True, diff="", score=100.0)
    some = _att(TARGET, TARGET[:-1] + ["addiu v0,zero,1"])
    assert residual_classes.key(done) < residual_classes.key(some) < residual_classes.key(bad)


def test_search_uses_the_key_it_is_given():
    # Fire test for the wiring: with the class key the width-fixing child is kept and reported best.
    parent_src, child_src = "PARENT", "CHILD"
    atts = {parent_src: _att(TARGET, ["lw v0,0x10(a0)", "addiu v0,v0,1", "sll v0,v0,16", "sra v0,v0,16",
                                      "sw v0,0x10(a0)", "lw t6,0x14(a0)", "jr ra", "nop"]),
            child_src: _att(TARGET, ["lw v1,0x10(a0)", "addiu v1,v1,1", "move v0,v1", "sw v1,0x10(a0)",
                                     "lw t7,0x14(a0)", "addu t7,t7,zero", "move v0,t7", "jr ra", "nop"])}
    for a in atts.values():
        a.source_attribution = None
    edit = site_edits.Edit("decl", "s16 -> s32", 1, 0, len(parent_src), child_src)
    original = site_edits.propose
    site_edits.propose = lambda src, fn, diff, attr, **kw: ([edit], {}) if src == parent_src else ([], {})
    try:
        old = site_edits.search(lambda code, label, parent: atts[code], parent_src, "f", budget=4, depth=2)
        new = site_edits.search(lambda code, label, parent: atts[code], parent_src, "f", budget=4, depth=2,
                                key=residual_classes.key)
    finally:
        site_edits.propose = original
    assert old["source"] == parent_src
    assert new["source"] == child_src


def test_focus_ranks_the_class_line_ahead_of_heavier_knock_on_lines():
    # Class-key trial: 145 of 207 searches stalled on width because the four heaviest lines were
    # knock-on. Candidate listing: line 20 carries three shifted loads (and is charged the three target
    # loads they replace), line 30 the extension pair.
    target = ["lw v0,0x10(a0)", "addiu v0,v0,1", "lw t6,0x14(a0)", "lw t7,0x18(a0)", "lw t8,0x1c(a0)", "jr ra", "nop"]
    cand = ["lw v0,0x10(a0)", "addiu v0,v0,1", "lw t9,0x14(a0)", "lw t0,0x18(a0)", "lw t1,0x1c(a0)",
            "sll v0,v0,16", "sra v0,v0,16", "jr ra", "nop"]
    diff = _diff(target, cand)
    lines = {1: 10, 2: 10, 3: 20, 4: 20, 5: 20, 6: 30, 7: 30, 8: 40, 9: 40}   # candidate listing -> source line
    attribution = {"status": "verified",
                   "instructions": [{"normalized_line": k, "candidate_line": v} for k, v in lines.items()]}
    plain, _ = site_edits.site_lines(diff, attribution)
    assert plain.most_common(1)[0][0] == 20                         # knock-on is heaviest
    only = residual_classes.focus(diff)
    assert only is not None and only("sra v0,v0,16") and not only("lw t9,0x14(a0)")
    focused, _ = site_edits.site_lines(diff, attribution, only)
    assert set(focused) == {30}


def test_renamed_or_moved_extension_is_not_a_width_fault():
    # Fwave / __osSumcalc (focus trial): the same `andi ..,0xffff` in another register and place.
    target = ["lw t1,8(sp)", "sw t4,0xc(sp)", "andi t2,t1,0xffff", "sw t0,4(sp)", "jr ra", "nop"]
    cand = ["lw t2,8(sp)", "sw t1,4(sp)", "andi t3,t2,0xffff", "jr ra", "nop"]
    assert residual_classes.counts(_diff(target, cand))["width"] == 0
    wider = cand[:2] + ["sll t3,t3,16", "sra t3,t3,16"] + cand[2:]
    assert residual_classes.counts(_diff(target, wider))["width"] == 1


def test_byte_packing_shifts_are_not_extensions():
    # __osContRamWrite: (b0 << 24) | (b1 << 16) | ... has lone sll 0x18 / sll 0x10 feeding `or`.
    packed = ["lbu t4,0(t3)", "lbu t6,1(t3)", "sll t5,t4,0x18", "sll t8,t6,0x10", "or t9,t5,t8", "jr ra", "nop"]
    word = ["lw t9,0(t3)", "jr ra", "nop"]
    assert residual_classes.counts(_diff(word, packed))["width"] == 0
    sext = ["lh t6,0(a0)", "addiu t6,t6,1", "sll t7,t6,0x10", "sra t8,t7,0x10", "jr ra", "nop"]
    plain = ["lh t6,0(a0)", "addiu t6,t6,1", "jr ra", "nop"]
    assert residual_classes.counts(_diff(plain, sext))["width"] == 1


def test_stack_relative_constants_are_frame_not_operand():
    # osMotorStart copy-back merge (composed-edits amendment 8): frame offsets moved, no real constant did.
    t = ["addiu sp,sp,-0x50", "addiu t8,sp,0x1c", "li t5,4", "jr ra", "addiu sp,sp,0x50"]
    c = ["addiu sp,sp,-0x60", "addiu t8,sp,0x2c", "li t5,4", "jr ra", "addiu sp,sp,0x60"]
    k = residual_classes.counts(_diff(t, c))
    assert k["operand"] == 0 and k["frame"] == 2
    wrong = c[:2] + ["li t5,5"] + c[3:]
    assert residual_classes.counts(_diff(t, wrong))["operand"] == 1


def test_merge_that_only_moves_frame_offsets_ranks_ahead():
    # Parent and child as the class key saw them: equal through operand once frame offsets are
    # excluded, fewer instruction and register faults in the child -> the child must be kept.
    t = ["addiu sp,sp,-0x50", "lw t4,0x4c(sp)", "addiu t5,t4,1", "sw t5,0x4c(sp)", "slt at,t5,t7", "jr ra", "nop"]
    parent = ["addiu sp,sp,-0x60", "lw t4,0x5c(sp)", "addiu t5,t4,1", "sw t5,0x24(sp)", "lw t6,0x24(sp)",
              "sw t5,0x5c(sp)", "slt at,t6,t7", "jr ra", "nop"]
    child = ["addiu sp,sp,-0x60", "lw t4,0x5c(sp)", "addiu t5,t4,1", "sw t5,0x5c(sp)", "slt at,t5,t7", "jr ra", "nop"]
    assert residual_classes.key(_att(t, child)) < residual_classes.key(_att(t, parent))
