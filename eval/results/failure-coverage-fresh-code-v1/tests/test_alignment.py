"""Block-aware residual alignment and classification tests."""

from solver import alignment


def udiff(lines: list[str]) -> str:
    return "--- target\n+++ candidate\n@@ -1,9 +1,9 @@\n" + "\n".join(lines)


def test_unpaired_instruction_does_not_shift_later_memory_pairs():
    result = alignment.align_diff(udiff([
        "-nop",
        "-lbu v1,0x24(a0)",
        "+lbu v1,0(a0)",
        "-lh t8,0x18(v0)",
        "+lh t8,4(v0)",
    ]))
    assert result.faults.missing_target == 1
    assert result.faults.offset == 2


def test_branch_target_fault_is_named_separately():
    result = alignment.align_diff(udiff([
        "-beq v0,v1,.Ltarget",
        "+beq v0,v1,.Lother",
        " nop",
    ]))
    assert result.faults.branch_topology == 1
    assert result.faults.opcode_substitution == 0


def test_branch_shape_change_is_structural_and_topological():
    result = alignment.align_diff(udiff([
        "-b .Ltarget",
        "+beqz v1,.Ltarget",
        " nop",
    ]))
    assert result.faults.branch_topology == 1
    assert result.faults.opcode_substitution == 0
    assert result.faults.structural == 1


def test_jump_table_relocation_is_not_generic_structure():
    result = alignment.align_diff(udiff([
        "-lui at,%hi(jtbl_800E08BC)",
        "+lui at,%hi(.rodata)",
    ]))
    assert result.faults.relocation == 1
    assert result.faults.jump_table == 1
    assert result.faults.structural == 0


def test_delay_slot_difference_is_explicit():
    result = alignment.align_diff(udiff([
        " beqz a0,.L1",
        "-addiu v0,zero,1",
        "+nop",
        " .L1:",
        " jr ra",
        " nop",
    ]))
    assert result.faults.delay_slot == 1


def test_inserted_block_does_not_shift_following_block():
    target = [
        "beqz a0,.L1", "nop", "addiu v0,zero,1", "b .Ljoin", "nop",
        ".L1:", "addiu v0,zero,2", ".Ljoin:", "lw v1,0x24(a1)",
        "jr ra", "nop",
    ]
    candidate = [
        "beqz a0,.L1", "nop", "b .Ljoin", "nop",
        ".L1:", "addiu v0,zero,2", ".Ljoin:", "lw v1,0x20(a1)",
        "jr ra", "nop",
    ]
    result = alignment.align_streams(target, candidate)
    assert result.faults.missing_target == 1
    assert result.faults.offset == 1


def test_repeated_identical_opcodes_surface_ambiguity():
    result = alignment.align_streams(
        ["addu t0,t1,t2", "addu t0,t1,t2", "jr ra", "nop"],
        ["addu t0,t1,t2", "jr ra", "nop"])
    assert result.faults.missing_target == 1
    assert result.faults.ambiguous >= 1


def test_only_unambiguous_substitutions_enter_high_confidence_pairs():
    result = alignment.align_diff(udiff([
        "-lw v0,0x24(a0)",
        "+lw v0,0x20(a0)",
    ]))
    assert result.high_confidence_pairs == [
        ("lw v0,0x24(a0)", "lw v0,0x20(a0)")]


def test_safe_offset_pair_rejects_changed_destination_register():
    diff = udiff([
        "-lw v0,0x24(a0)",
        "+lw v1,0x20(a0)",
    ])
    assert alignment.safe_offset_pairs(diff) == []


def test_safe_memory_pair_accepts_width_but_not_load_store_flip():
    width = udiff(["-lw v0,0x24(a0)", "+lh v0,0x24(a0)"])
    direction = udiff(["-lw v0,0x24(a0)", "+sw v0,0x24(a0)"])
    assert alignment.safe_memory_pairs(width) == [
        ("lw v0,0x24(a0)", "lh v0,0x24(a0)")]
    assert alignment.safe_memory_pairs(direction) == []


def test_real_random_next_secondary_residual_is_not_misread_as_layout():
    """The checked-in escalation case: six register faults and one missing
    target store, with no offset evidence.  An aligner that drifts at the
    missing store can manufacture a layout repair from this residual."""
    diff = udiff([
        "-lui v1,%hi(gSecondaryRngIndex)",
        "-addiu v1,v1,%lo(gSecondaryRngIndex)",
        "-lhu t6,0(v1)",
        "+lui a0,%hi(gSecondaryRngIndex)",
        "+addiu a0,a0,%lo(gSecondaryRngIndex)",
        "+lhu v1,0(a0)",
        " lui v0,%hi(gRandomTable)",
        "-addiu t8,t6,1",
        "-andi t9,t8,0xff",
        "-sh t8,0(v1)",
        "+addiu t7,v1,1",
        "+andi t9,t7,0xff",
        " andi t0,t9,0xffff",
        "-sh t9,0(v1)",
        "+sh t9,0(a0)",
        " addu v0,v0,t0",
        " lbu v0,%lo(gRandomTable)(v0)",
        " jr ra",
    ])
    result = alignment.align_diff(diff)
    assert result.faults.register == 6
    assert result.faults.missing_target == 1
    assert result.faults.offset == result.faults.ambiguous == 0
    assert alignment.safe_offset_pairs(diff) == []
