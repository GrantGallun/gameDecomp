"""Fixed-point symbolic value propagation tests."""

from solver import dataflow


def access_descriptions(asm: str) -> list[str | None]:
    result = dataflow.analyse(asm)
    return [a.address.describe() if a.address is not None else None
            for _i, a in sorted(result.accesses.items())]


def test_stack_load_identity_does_not_oscillate_back_to_known(monkeypatch):
    # Reduced from __osPfsDeclearPage binary intake. Without monotone weakening,
    # the loop input alternates between unknown and load(stack)+1 forever.
    asm = '''glabel f
    lw t0,0(sp)
    addiu t0,t0,1
    sw t0,0(sp)
.Lloop:
    lw t0,0(sp)
    addiu t0,t0,1
    sw t0,0(sp)
    bnez a0,.Lloop
    nop
    jr ra
    nop
'''
    transfer = dataflow._transfer
    visits = []
    def bounded(block, *args, **kwargs):
        visits.append(block.id)
        assert len(visits) < 30, 'symbolic loop did not converge'
        return transfer(block, *args, **kwargs)
    monkeypatch.setattr(dataflow, '_transfer', bounded)
    result = dataflow.analyse(asm)
    loop = next(block for block in result.graph.blocks.values() if '.Lloop' in block.labels)
    assert 0 not in result.block_in[loop.id].stack
    assert result.block_in[loop.id].registers['sp'] == dataflow.Value.address('stack')


def test_entry_backedge_does_not_replace_the_initial_boundary(monkeypatch):
    asm = '''glabel f
    addiu a0,a0,4
    bnez a1,f
    nop
    lw v0,0(a0)
    jr ra
    nop
'''
    transfer = dataflow._transfer
    calls = []
    def bounded(*args, **kwargs):
        calls.append(1)
        assert len(calls) < 20
        return transfer(*args, **kwargs)
    monkeypatch.setattr(dataflow, '_transfer', bounded)
    result = dataflow.analyse(asm)
    assert 'a0' not in result.block_in[result.graph.entry].registers
    assert next(a for a in result.accesses.values() if a.opcode == 'lw').address is None


def test_value_invariant_across_diamond_survives_join():
    asm = """\
glabel f
    move $s0, $a0
    beqz $a1, .Lelse
    nop
    addiu $t0, $zero, 1
    b .Ljoin
    nop
.Lelse:
    addiu $t0, $zero, 2
.Ljoin:
    lw $v0, 0x24($s0)
    jr $ra
    nop
"""
    assert "param0+0x24" in access_descriptions(asm)


def test_path_dependent_value_becomes_unknown_at_join():
    asm = """\
glabel f
    beqz $a2, .Lelse
    nop
    move $s0, $a0
    b .Ljoin
    nop
.Lelse:
    move $s0, $a1
.Ljoin:
    lw $v0, 0x10($s0)
    jr $ra
    nop
"""
    accesses = dataflow.analyse(asm).accesses
    load = next(a for a in accesses.values() if a.opcode == "lw")
    assert load.address is None


def test_loop_fixed_point_preserves_invariant_base():
    asm = """\
glabel f
    move $s0, $a0
    move $t0, $zero
.Lhead:
    lw $v0, 0x18($s0)
    addiu $t0, $t0, 1
    bne $t0, $a1, .Lhead
    nop
    jr $ra
    nop
"""
    assert "param0+0x18" in access_descriptions(asm)


def test_stack_spill_and_reload_preserve_symbolic_value():
    asm = """\
glabel f
    addiu $sp, $sp, -0x20
    sw $a0, 0x10($sp)
    lw $s0, 0x10($sp)
    lw $v0, 0x8($s0)
    jr $ra
    nop
"""
    descriptions = access_descriptions(asm)
    assert "stack-0x10" in descriptions
    assert "param0+0x8" in descriptions


def test_call_clobber_happens_after_delay_slot():
    asm = """\
glabel f
    move $t0, $a0
    jal callee
    lw $a1, 0x4($t0)
    lw $v0, 0x8($t0)
    jr $ra
    nop
"""
    result = dataflow.analyse(asm)
    ordered = [result.accesses[i] for i in sorted(result.accesses)]
    assert ordered[0].address.describe() == "param0+0x4"
    assert ordered[1].address is None


def test_loaded_pointer_gets_stable_mechanical_identity():
    asm = """\
glabel f
    lw $s0, 0x10($a0)
    lw $v0, 0x24($s0)
    jr $ra
    nop
"""
    descriptions = access_descriptions(asm)
    assert descriptions == ["param0+0x10", "load(param0+0x10)+0x24"]


def test_branch_likely_delay_slot_fact_is_not_claimed_on_both_paths():
    asm = """\
glabel f
    beqzl $a1, .Ljoin
    move $s0, $a0
.Ljoin:
    lw $v0, 0x4($s0)
    jr $ra
    nop
"""
    load = next(a for a in dataflow.analyse(asm).accesses.values()
                if a.opcode == "lw")
    assert load.address is None


def test_real_random_next_secondary_resolves_all_memory_identities():
    asm = """\
glabel randomNextSecondary
    lui $v1, %hi(gSecondaryRngIndex)
    addiu $v1, $v1, %lo(gSecondaryRngIndex)
    lhu $t6, 0x0($v1)
    lui $v0, %hi(gRandomTable)
    addiu $t8, $t6, 0x1
    andi $t9, $t8, 0xFF
    sh $t8, 0x0($v1)
    andi $t0, $t9, 0xFFFF
    sh $t9, 0x0($v1)
    addu $v0, $v0, $t0
    lbu $v0, %lo(gRandomTable)($v0)
    jr $ra
    nop
"""
    accesses = dataflow.analyse(asm).accesses.values()
    assert [a.address.describe() if a.address else None for a in accesses] == [
        "gSecondaryRngIndex", "gSecondaryRngIndex",
        "gSecondaryRngIndex", "gRandomTable",
    ]
