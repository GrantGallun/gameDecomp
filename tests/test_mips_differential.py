from solver import mips_differential as differential


ARITIES = {"observe": 2}


def test_not_alias_and_nor_have_32_bit_values_and_input_provenance():
    program=differential.Program.parse('complement','not v0,a0\nnor v1,a0,a1\njr ra\nnop')
    for left,right in ((0,0),(0xffffffff,0),(0x80000000,1),(0x12345678,0x87654321)):
        case=differential.TestCase('complement',1,entry_registers=(('a0',left),('a1',right)))
        run=differential.execute_case(program,case,return_registers=('v0','v1'))
        assert run.status=='returned'
        assert run.return_values=={'v0':(~left)&0xffffffff,'v1':(~(left|right))&0xffffffff}
        assert [name for name,_,_ in run.trace[0].reads]==['a0']
        assert [name for name,_,_ in run.trace[1].reads]==['a0','a1']
        assert 'not(entry a0=' in run.trace[0].writes[0][2]


def _case(**kwargs):
    return differential.TestCase("unit", 123, **kwargs)


def test_big_endian_signed_memory_and_delay_slot_branch():
    assembly = """
        lh t0,0(a0)
        bnez t0,10
        addiu t1,zero,7
        li t1,99
        sw t1,4(a0)
        jr ra
        nop
    """
    program = differential.Program.parse("branch", assembly)
    case = _case(player_writes=((0, 2, 0xFFFF),))
    result = differential.compare_programs(program, program, case)
    assert result.status == "passed", result.first_divergence
    assert result.target.instruction_count == 6
    assert result.target.writes[-1].value == 7


def test_equivalent_register_allocation_passes():
    target = """
        lw t0,0(a0)
        addiu t1,t0,1
        sw t1,4(a0)
        jr ra
        nop
    """
    candidate = """
        lw v1,0(a0)
        addiu t7,v1,1
        sw t7,4(a0)
        jr ra
        nop
    """
    rows = differential.run_suite(
        target, candidate, (_case(player_writes=((0, 4, 41),)),))
    assert rows[0].status == "passed"
    assert rows[0].first_divergence == ""


def test_variable_shifts_use_low_five_bits_and_preserve_signedness():
    assembly = """
        li t0,0x80000001
        li t1,33
        sllv v0,t0,t1
        srlv v1,t0,t1
        srav a0,t0,t1
        jr ra
        nop
    """
    program = differential.Program.parse("variable-shifts", assembly)

    run = differential.execute_case(
        program, _case(), return_registers=("v0", "v1", "a0"))

    assert run.status == "returned"
    assert run.return_values == {
        "v0": 0x00000002,
        "v1": 0x40000000,
        "a0": 0xC0000000,
    }


def test_double_conversion_multiply_and_move_preserve_register_pairs():
    assembly = """
        li t0,4
        mtc1 t0,f4
        cvt.d.w f6,f4
        mul.d f12,f12,f6
        jr ra
        mov.d f0,f12
    """
    high, low = differential._float64_words(1.5)
    case = differential.TestCase(
        "double", 1,
        entry_registers=(("f12", low), ("f13", high)))
    program = differential.Program.parse("double", assembly)

    run = differential.execute_case(
        program, case, return_registers=("f0", "f1"))

    assert run.status == "returned"
    assert differential._float64_from_words(
        run.return_values["f1"], run.return_values["f0"]) == 6.0


def test_unconditional_nop_self_loop_is_a_comparable_nonreturn_terminal():
    assembly = """
        li t0,7
    idle:
        b idle
        nop
    """

    result = differential.run_suite(assembly, assembly, (_case(),))[0]

    assert result.status == "passed"
    assert result.target.status == "nonreturn"
    assert result.candidate.status == "nonreturn"
    program = differential.Program.parse("idle", assembly)
    report = differential.coverage_report(program, (result.target,))
    assert report.complete


def test_returning_candidate_does_not_match_nonreturning_target():
    target = """
    idle:
        b idle
        nop
    """
    candidate = """
        jr ra
        nop
    """

    result = differential.run_suite(target, candidate, (_case(),))[0]

    assert result.status == "failed"
    assert "terminal behavior differs" in result.reasons[0]


def test_self_loop_with_effectful_delay_slot_remains_a_step_limit():
    assembly = """
    loop:
        b loop
        sw zero,0(a0)
    """
    program = differential.Program.parse("effectful-loop", assembly)

    run = differential.execute_case(program, _case(), max_steps=8)

    assert run.status == "step_limit"


def test_call_delay_slot_arguments_are_compared_as_normalized_pointers():
    target = """
        addiu sp,sp,-8
        sw ra,4(sp)
        addiu a0,a0,0x40
        jal observe
        li a1,7
        lw ra,4(sp)
        jr ra
        addiu sp,sp,8
    """
    candidate = """
        addiu sp,sp,-8
        sw ra,4(sp)
        addiu a0,a0,0x1c
        jal observe
        li a1,7
        lw ra,4(sp)
        jr ra
        addiu sp,sp,8
    """
    result = differential.run_suite(
        target, candidate, (_case(),), call_arities=ARITIES)[0]
    assert result.status == "failed"
    assert "observe('player+0x40', '0x00000007')" in result.first_divergence
    assert "observe('player+0x1c', '0x00000007')" in result.first_divergence
    feedback = differential.repair_feedback(result)
    assert "DIFFERENTIAL EXECUTION: FAILED" in feedback
    assert "first divergence: call #0 differs" in feedback
    assert "call traceback" in feedback
    assert "[00] ! target: observe(player+0x40, 0x00000007)" in feedback
    assert "candidate: observe(player+0x1c, 0x00000007)" in feedback


def test_wrong_written_value_fails_even_when_address_and_width_match():
    target = """
        lw t0,0(a0)
        lw t1,4(a0)
        addu t2,t0,t1
        sw t2,8(a0)
        jr ra
        nop
    """
    candidate = """
        lw t0,0(a0)
        lw t1,4(a0)
        addu t2,t0,t0
        sw t2,8(a0)
        jr ra
        nop
    """
    case = _case(player_writes=((0, 4, 2), (4, 4, 5)))
    result = differential.run_suite(target, candidate, (case,))[0]
    assert result.status == "failed"
    assert result.reasons == ("final persistent memory differs",)
    assert "target player+0x8/4=0x7" in result.first_divergence
    assert "candidate player+0x8/4=0x4" in result.first_divergence
    assert "earliest persistent-write divergence" not in \
        differential.repair_feedback(result)


def test_unsupported_candidate_is_inconclusive_not_passed():
    target = """
        sw zero,0(a0)
        jr ra
        nop
    """
    candidate = """
        add.s f0,f2,f4
        sw zero,0(a0)
        jr ra
        nop
    """
    result = differential.run_suite(target, candidate, (_case(),))[0]
    assert result.status == "inconclusive"
    assert "unsupported" in result.reasons[0]
    assert "execution status differs" in result.first_divergence


def test_cop1_raw_bit_transfers_and_memory_are_compared():
    target = """
        mtc1 zero,f4
        swc1 f4,4(a0)
        lwc1 f6,0(a0)
        mfc1 t0,f6
        sw t0,8(a0)
        jr ra
        nop
    """
    case = _case(player_writes=((0, 4, 0x3F800000),))

    result = differential.run_suite(target, target, (case,))[0]

    assert result.status == "passed"
    assert result.target.persistent_state == result.candidate.persistent_state
    assert result.target.writes[0].value == 0
    assert result.target.writes[1].value == 0x3F800000


def test_coverage_explorer_inverts_equality_to_traced_load_value():
    assembly = """
        lh t0,0(a0)
        beq t0,a1,equal
        nop
        sw zero,4(a0)
        b done
        nop
    equal:
        sw a1,4(a0)
    done:
        jr ra
        nop
    """
    seed = _case(
        player_writes=((0, 2, 0),), entry_registers=(("a1", 0x1234),))

    explored = differential.explore_coverage(
        assembly, (seed,), mutable_entry_registers=(), max_cases=2)

    assert explored.report.complete
    assert len(explored.cases) == 2
    assert explored.cases[1].player_writes[-1] == (0, 2, 0x1234)
    assert "edge1t" in explored.cases[1].name


def test_predicate_gradient_follows_load_through_overwriting_store():
    assembly = """
        lui t0,%hi(gSource)
        lbu t0,%lo(gSource)(t0)
        sb t0,0(a0)
        lbu t1,0(a0)
        li t2,5
        beq t1,t2,equal
        nop
        sw zero,4(a0)
        b done
        nop
    equal:
        sw t2,4(a0)
    done:
        jr ra
        nop
    """
    seed = _case(global_writes=(("gSource", 1, 0),))

    explored = differential.explore_coverage(
        assembly, (seed,), mutable_entry_registers=(), max_cases=2)

    assert explored.report.complete
    assert explored.cases[1].global_writes[-1] == ("gSource", 1, 5)
    assert explored.cases[1].player_writes == ()


def test_coverage_bit_basis_reaches_mask_after_shift():
    """A later masked bit requires the inverse one-hot bit at function entry."""
    assembly = """
        lui t3,%hi(gSeed)
        lw t0,%lo(gSeed)(t3)
        sll t0,t0,1
        lui t2,0x4800
        and t1,t0,t2
        lui t4,0x0800
        bne t1,t4,done
        nop
        li v0,1
    done:
        jr ra
        nop
    """
    seed = _case(global_writes=(("gSeed", 4, 0),))

    explored = differential.explore_coverage(
        assembly, (seed,), mutable_entry_registers=(), max_cases=200)

    assert explored.report.complete
    assert any(("gSeed", 4, 0x04000000) in case.global_writes
               for case in explored.cases)


def test_program_specific_jump_table_words_drive_indirect_branches():
    target = """
        lui t0,%hi(targetTable)
        lw t1,%lo(targetTable)(t0)
        jr t1
        nop
        li v0,7
        jr ra
        nop
# MIPS_DIFF_DATA targetTable 0x0 0x10
    """
    candidate = """
        lui t0,%hi(.rodata)
        lw t1,%lo(.rodata)(t0)
        jr t1
        nop
        nop
        li v0,7
        jr ra
        nop
# MIPS_DIFF_DATA .rodata 0x0 0x14
    """

    result = differential.run_suite(
        target, candidate, (_case(),), return_registers=("v0",))[0]

    assert result.status == "passed"
    assert result.target.return_values == {"v0": 7}
    assert result.candidate.return_values == {"v0": 7}


def test_coverage_includes_every_annotated_switch_destination():
    assembly = """
        lui t0,%hi(table)
        addu t0,t0,a1
        lw t1,%lo(table)(t0)
        jr t1
        nop
        li v0,7
        jr ra
        nop
        li v0,8
        jr ra
        nop
# MIPS_DIFF_DATA table 0x0 0x14
# MIPS_DIFF_DATA table 0x4 0x20
    """
    program = differential.Program.parse("switch", assembly)
    first = differential.execute_case(
        program, differential.TestCase("first", 1, entry_registers=(("a1", 0),)))
    second = differential.execute_case(
        program, differential.TestCase("second", 1, entry_registers=(("a1", 4),)))
    partial = differential.coverage_report(program, (first,))
    assert not partial.complete
    assert (8, "li v0,8") in partial.missing_instructions
    assert differential.coverage_report(program, (first, second)).complete
    assert not differential._instruction_dominates(program, 5, 8)


def test_switch_boundaries_include_non_power_of_two_selectors():
    assembly = "jr ra\nnop\n" + "\n".join(
        f"# MIPS_DIFF_DATA table {index * 4:#x} 0x0" for index in range(7))
    program = differential.Program.parse("switch", assembly)
    assert {0, 1, 2, 3, 4, 5, 6} <= set(differential._boundary_values(program, 4))


def test_scratch_inputs_are_seeded_without_creating_fake_global_symbols():
    case = differential.TestCase("scratch", 1, global_writes=(("@arg1+0xe", 2, 7),),
                                 entry_registers=(("a1", differential.ARG_POINTER_BASES["a1"]),))
    assembly = "lhu v0,0xe(a1)\njr ra\nnop"
    result = differential.run_suite(assembly, assembly, (case,), return_registers=("v0",))[0]
    assert result.status == "passed"
    assert result.target.return_values["v0"] == 7
    assert not differential._case_symbols(case)
    import pytest
    with pytest.raises(ValueError, match="exceeds mapped"):
        differential._scratch_address("@arg1+0x1fff", 4)


def test_directed_comparison_can_mutate_linked_node_in_scratch_memory():
    assembly = """
        lhu t0,0xe(a1)
        slt at,t0,a2
        bnez at,done
        nop
        lw v0,4(a1)
        jr ra
        nop
    done:
        li v0,0
        jr ra
        nop
    """
    case = differential.TestCase("node", 1,
        global_writes=(("@arg1+0xe", 2, 0), ("@arg1+0x4", 4, 0)),
        entry_registers=(("a1", differential.ARG_POINTER_BASES["a1"]), ("a2", 1)))
    program = differential.Program.parse("node", assembly)
    run = differential.execute_case(program, case)
    mutations = differential._predicate_mutations(program, case, run, ((2, False),))
    assert any(("@arg1+0xe", 2, 1) in m.global_writes for m in mutations)
    assert any(dict(m.entry_registers)["a2"] == 0 for m in mutations)
    report = differential.explore_coverage(assembly, (case,), max_cases=64,
                                          mutable_entry_registers=()).report
    assert report.complete


def test_unknown_register_jump_cannot_report_complete_coverage():
    program = differential.Program.parse("unknown", "jr a1\nnop")
    run = differential.execute_case(
        program, differential.TestCase(
            "case", 1, entry_registers=(("a1", differential.RETURN_SENTINEL),)))
    report = differential.coverage_report(program, (run,))
    assert report.instruction_complete
    assert not report.complete
    assert report.unresolved_indirect_jumps == (0,)
    assert report.to_dict()["status"] == "partial"


def test_linker_symbol_and_base_plus_offset_alias_same_physical_byte():
    target = """
        lui t0,%hi(secondState)
        sb zero,%lo(secondState)(t0)
        jr ra
        nop
# MIPS_DIFF_SYMBOL secondState 0x80100004
    """
    candidate = """
        lui t0,%hi(firstState)
        sb zero,%lo(firstState+0x4)(t0)
        jr ra
        nop
# MIPS_DIFF_SYMBOL firstState 0x80100000
    """

    result = differential.run_suite(target, candidate, (_case(),))[0]

    assert result.status == "passed"
    assert result.target.writes[0].raw_address == 0x80100004
    assert result.candidate.writes[0].raw_address == 0x80100004


def test_predicate_gradient_can_override_opaque_call_return():
    assembly = """
        move s0,a0
        move s1,ra
        jal probe
        nop
        beqz v0,zero
        nop
        sw v0,0(s0)
        b done
        nop
    zero:
        sw zero,0(s0)
    done:
        jr s1
        nop
    """
    seed = differential.TestCase(
        "call-nonzero", 7, call_returns=(("probe", 0, 1),))

    explored = differential.explore_coverage(
        assembly, (seed,), call_arities={"probe": 0},
        mutable_entry_registers=(), max_cases=2)

    assert explored.report.complete
    assert explored.cases[1].call_returns == (("probe", 0, 0),)


def test_faulting_opaque_call_return_is_steered_to_mapped_scratch():
    assembly = """
        move s0,ra
        jal allocate
        nop
        sw zero,0(v0)
        jr s0
        nop
    """
    seed = differential.TestCase("allocator-default", 7)

    explored = differential.explore_coverage(
        assembly, (seed,), call_arities={"allocate": 0},
        mutable_entry_registers=(), max_cases=10)

    assert explored.report.complete
    assert any(
        ("allocate", 0, differential.ARG_POINTER_BASES["a1"])
        in case.call_returns for case in explored.cases)


def test_external_call_captures_o32_stack_arguments_after_a3():
    assembly = """
        addiu sp,sp,-0x20
        sw ra,0x1c(sp)
        li t0,5
        sw t0,0x10(sp)
        li t0,6
        sw t0,0x14(sp)
        li a0,1
        li a1,2
        li a2,3
        li a3,4
        jal sixArgs
        nop
        lw ra,0x1c(sp)
        jr ra
        addiu sp,sp,0x20
    """

    run = differential.execute_case(
        differential.Program.parse("six-args", assembly), _case(),
        call_arities={"sixArgs": 6})

    assert run.status == "returned"
    assert run.calls[0].raw_arguments == (1, 2, 3, 4, 5, 6)
    assert "o32 stack argument a4" in run.calls[0].argument_provenance[4]


def test_coverage_keeps_path_conjunction_that_adds_no_edge_by_itself():
    assembly = """
        lui t0,%hi(gOverwrite)
        lbu t0,%lo(gOverwrite)(t0)
        beqz t0,skip_store
        nop
        li t1,1
        sb t1,0(a0)
    skip_store:
        lbu t1,0(a0)
        li t2,5
        beq t1,t2,equal
        nop
        sw zero,4(a0)
        b done
        nop
    equal:
        sw t2,4(a0)
    done:
        jr ra
        nop
    """
    seed = _case(
        player_writes=((0, 1, 0),),
        global_writes=(("gOverwrite", 1, 1),))

    explored = differential.explore_coverage(
        assembly, (seed,), mutable_entry_registers=(), max_cases=100)

    assert explored.report.complete
    witness = [case for case in explored.cases
               if any(write == ("gOverwrite", 1, 0)
                      for write in case.global_writes)
               and any(write == (0, 1, 5)
                       for write in case.player_writes)]
    assert witness


def test_global_hi_lo_relocation_and_memory_are_stable():
    assembly = """
        lui t0,%hi(gCounter)
        lh t1,%lo(gCounter)(t0)
        addiu t1,t1,1
        sh t1,%lo(gCounter)(t0)
        jr ra
        nop
    """
    case = _case(global_writes=(("gCounter", 2, 0x7FFF),))
    result = differential.run_suite(assembly, assembly, (case,))[0]
    assert result.status == "passed"
    assert result.target.writes[0].address == "&gCounter"
    assert result.target.writes[0].value == 0x8000

    self_base_load = """
        lui t0,%hi(gCounter)
        lhu t0,%lo(gCounter)(t0)
        jr ra
        nop
    """
    traced = differential.run_suite(
        self_base_load, self_base_load, (case,))[0]
    assert "address from i0 lui %hi(gCounter)" in traced.target.trace[1].effect
    assert "address from i1 lhu" not in traced.target.trace[1].effect


def test_global_relocation_addend_uses_the_base_symbol_region():
    assembly = """
        lui t0,%hi(gViewportStates+1)
        lbu v0,%lo(gViewportStates+1)(t0)
        jr ra
        nop
    """
    case = differential.TestCase(
        "addend", 1,
        global_writes=(("gViewportStates+0x1", 1, 0x5A),))

    run = differential.execute_case(
        differential.Program.parse("addend", assembly), case,
        return_registers=("v0",))

    assert run.status == "returned"
    assert run.return_values == {"v0": 0x5A}
    assert run.trace[1].effect.startswith("load &gViewportStates+0x1/1=")


def test_static_relocation_addend_expands_global_region():
    assembly = """
        lui at,%hi(gViewportStates)
        sb zero,%lo(gViewportStates+0x22e)(at)
        jr ra
        nop
    """

    run = differential.execute_case(
        differential.Program.parse("static-extent", assembly), _case())

    assert run.status == "returned"
    assert run.writes[0].address == "&gViewportStates+0x22e"


def test_known_global_array_region_allows_large_record_offsets():
    assembly = """
        lui t0,%hi(gRacePlayers)
        addiu t0,t0,%lo(gRacePlayers)
        lw v0,0x568(t0)
        jr ra
        nop
    """

    run = differential.execute_case(
        differential.Program.parse("large-global", assembly), _case())

    assert run.status == "returned"
    assert run.trace[2].effect.startswith("load &gRacePlayers+0x568/4=")


def test_coverage_explorer_uses_faulting_prefix_to_mutate_global_subfield():
    assembly = """
        lui t0,%hi(gGate)
        lbu t0,%lo(gGate)(t0)
        bnez t0,done
        nop
        lui t0,%hi(gRacePlayers)
        addiu t0,t0,%lo(gRacePlayers)
        lbu t0,0x509(t0)
        sll t0,t0,2
        lui t1,%hi(gSmallTable)
        addiu t1,t1,%lo(gSmallTable)
        addu t1,t1,t0
        lbu v0,0(t1)
    done:
        jr ra
        nop
    """
    seed = _case(global_writes=(("gGate", 1, 1),))

    explored = differential.explore_coverage(
        assembly, (seed,), mutable_entry_registers=(), max_cases=1000)

    assert explored.report.complete
    assert any(
        location == "gRacePlayers+0x509"
        for case in explored.cases
        for location, _width, _value in case.global_writes)


def test_coverage_explorer_can_turn_loaded_word_into_valid_pointer():
    assembly = """
        lui t0,%hi(gTask)
        lw t0,%lo(gTask)(t0)
        sw zero,0x18(t0)
        jr ra
        nop
    """

    explored = differential.explore_coverage(
        assembly, (_case(),), mutable_entry_registers=(), max_cases=100)

    assert explored.report.complete
    assert any(any(write[0] == "gTask" and
                   write[2] in differential.ARG_POINTER_BASES.values()
                   for write in case.global_writes)
               for case in explored.cases)


def test_coverage_explorer_can_choose_non_aliasing_scratch_pointer():
    assembly = """
        lui t0,%hi(gOutput)
        lw t0,%lo(gOutput)(t0)
        sw zero,0(t0)
        lbu t1,0(a0)
        bnez t1,kept
        nop
        li v0,1
    kept:
        jr ra
        nop
    """
    seed = _case(player_writes=((0, 1, 1),))

    explored = differential.explore_coverage(
        assembly, (seed,), mutable_entry_registers=(), max_cases=300)

    assert explored.report.complete
    assert any(("gOutput", 4, differential.ARG_POINTER_BASES["a1"])
               in case.global_writes for case in explored.cases)


def test_directed_progress_does_not_discard_fault_parent_needed_for_pointer_fix():
    assembly = """
        move s0,ra
        jal probe
        nop
        beqz v0,done
        nop
        lui t0,%hi(gPointer)
        lw t0,%lo(gPointer)(t0)
        sw zero,0(t0)
    done:
        jr s0
        nop
    """
    seed = _case(call_returns=(("probe", 0, 1),))

    explored = differential.explore_coverage(
        assembly, (seed,), call_arities={"probe": 0},
        mutable_entry_registers=(), max_cases=200)

    assert explored.report.complete
    assert any(any(write[0] == "gPointer" and
                   write[2] in differential.ARG_POINTER_BASES.values()
                   for write in case.global_writes)
               for case in explored.cases)
    assert explored.attempted_cases < 100


def test_step_limit_parent_gets_environment_repair_before_more_paths():
    assembly = """
        move s0,ra
        move s1,a0
        jal probe
        nop
        beqz v0,done
        nop
        lui t0,%hi(gPointer)
        lw t0,%lo(gPointer)(t0)
        li t1,-1
        sw t1,0(t0)
    loop:
        lw t2,0(s1)
        beqz t2,done
        nop
        addiu t2,t2,-1
        sw t2,0(s1)
        b loop
        nop
    done:
        jr s0
        nop
    """
    seed = _case(
        player_writes=((0, 4, 1),),
        global_writes=(("gPointer", 4, differential.PLAYER_BASE),),
        call_returns=(("probe", 0, 1),))

    explored = differential.explore_coverage(
        assembly, (seed,), call_arities={"probe": 0},
        mutable_entry_registers=(), max_cases=200, max_steps=50)

    assert explored.report.complete
    assert any(("gPointer", 4, differential.ARG_POINTER_BASES["a1"])
               in case.global_writes for case in explored.cases)
    assert explored.attempted_cases < 100


def test_newly_deeper_fault_preempts_remaining_sibling_mutations():
    assembly = """
        lui t0,%hi(gFirstPointer)
        lw t0,%lo(gFirstPointer)(t0)
        sw zero,0(t0)
        lui t1,%hi(gSecondPointer)
        lw t1,%lo(gSecondPointer)(t1)
        sw zero,0(t1)
        jr ra
        nop
    """
    seed = _case(global_writes=(("gFirstPointer", 4, 0),))

    explored = differential.explore_coverage(
        assembly, (seed,), mutable_entry_registers=(),
        max_cases=75, max_steps=50)

    assert explored.report.complete
    assert explored.attempted_cases <= 5
    assert any(
        any(write[0] == "gFirstPointer" and
            write[2] in differential.ARG_POINTER_BASES.values()
            for write in case.global_writes) and
        any(write[0] == "gSecondPointer" and
            write[2] in differential.ARG_POINTER_BASES.values()
            for write in case.global_writes)
        for case in explored.cases)


def test_predicate_solver_inverts_slti_boolean_to_source_boundary():
    assembly = """
        lh t0,0(a0)
        slti at,t0,0xa5
        beqz at,high
        nop
        li v0,1
    high:
        jr ra
        nop
    """
    seed = _case(player_writes=((0, 2, 0),))

    explored = differential.explore_coverage(
        assembly, (seed,), mutable_entry_registers=(), max_cases=10)

    assert explored.report.complete
    assert any((0, 2, 0xA5) in case.player_writes
               for case in explored.cases)


def test_sprintf_format_arguments_compare_initialized_string_contents():
    target = """
        addiu sp,sp,-8
        sw ra,4(sp)
        lui a1,%hi(targetFormat)
        addiu a1,a1,%lo(targetFormat)
        jal sprintf
        nop
        lw ra,4(sp)
        jr ra
        addiu sp,sp,8
        # MIPS_DIFF_BYTES targetFormat 25322e326400
    """
    candidate = target.replace(
        "targetFormat", "candidateFormat").replace(
            "# MIPS_DIFF_BYTES candidateFormat 25322e326400",
            "# MIPS_DIFF_BYTES candidateFormat 25322e326400")

    result = differential.run_suite(
        target, candidate, (_case(),), call_arities={"sprintf": 3})[0]

    assert result.status == "passed", result.first_divergence
    assert result.target.calls[0].arguments[1] == "cstr:25322e3264"
    assert result.candidate.calls[0].arguments[1] == "cstr:25322e3264"


def test_sprintf_format_arguments_reject_different_string_contents():
    target = """
        addiu sp,sp,-8
        sw ra,4(sp)
        lui a1,%hi(targetFormat)
        addiu a1,a1,%lo(targetFormat)
        jal sprintf
        nop
        lw ra,4(sp)
        jr ra
        addiu sp,sp,8
        # MIPS_DIFF_BYTES targetFormat 25322e326400
    """
    candidate = target.replace(
        "targetFormat", "candidateFormat").replace(
            "25322e326400", "256400")

    result = differential.run_suite(
        target, candidate, (_case(),), call_arities={"sprintf": 3})[0]

    assert result.status == "failed"
    assert "call #0 differs" in result.first_divergence


def test_candidate_raw_address_reads_target_initialized_rodata():
    target = """
        addiu sp,sp,-8
        sw ra,4(sp)
        lui a1,%hi(D_80001000)
        addiu a1,a1,%lo(D_80001000)
        jal sprintf
        nop
        lw ra,4(sp)
        jr ra
        addiu sp,sp,8
        # MIPS_DIFF_BYTES D_80001000 25322e326400
    """
    candidate = target.replace(
        "lui a1,%hi(D_80001000)\n"
        "        addiu a1,a1,%lo(D_80001000)",
        "li a1,0x80001000").replace(
            "        # MIPS_DIFF_BYTES D_80001000 25322e326400\n", "")

    result = differential.run_suite(
        target, candidate, (_case(),), call_arities={"sprintf": 3})[0]

    assert result.status == "passed", result.first_divergence
    assert result.candidate.calls[0].arguments[1] == "cstr:25322e3264"


def test_call_traceback_marks_same_call_with_different_entry_memory():
    target = """
        addiu sp,sp,-8
        sw ra,4(sp)
        sw zero,0(a0)
        jal observe
        move a0,a0
        lw ra,4(sp)
        jr ra
        addiu sp,sp,8
    """
    candidate = target.replace("sw zero,0(a0)", "sw zero,4(a0)")
    result = differential.run_suite(
        target, candidate, (_case(),), call_arities={"observe": 1})[0]
    trace = differential.call_traceback(result)
    assert "[00] ~ observe(player)" in trace
    assert "persistent memory differs at entry" in trace
    evidence = differential.causal_slice(result)
    assert "call-entry memory delta:" in evidence
    assert "initial seeded memory" in evidence
    assert "write#0 i2 player/4=0x0" in evidence


def test_resynchronization_exposes_two_independent_bad_call_arguments():
    target = """
        addiu sp,sp,-8
        sw ra,4(sp)
        jal observe
        move a0,a1
        jal observe
        move a0,a2
        lw ra,4(sp)
        jr ra
        addiu sp,sp,8
    """
    candidate = """
        addiu sp,sp,-8
        sw ra,4(sp)
        jal observe
        addiu a0,a1,1
        jal observe
        addiu a0,a2,2
        lw ra,4(sp)
        jr ra
        addiu sp,sp,8
    """
    case = differential.TestCase(
        "two-bad-calls", 23,
        entry_registers=(("a1", 0x1111), ("a2", 0x2222)))

    ordinary = differential.run_suite(
        target, candidate, (case,), call_arities={"observe": 1})[0]
    reports = differential.run_resynchronized_suite(
        target, candidate, (case,), call_arities={"observe": 1},
        max_divergences=3)
    report = reports[0]

    assert ordinary.status == "failed"
    assert [row.kind for row in report.observations] == ["call", "call"]
    assert [row.stage for row in report.observations] == [1, 2]
    assert report.terminal_status == "passed"
    assert report.stop_reason == \
        "counterfactual checkpoints align all observables"
    bundle = differential.render_divergence_bundle(reports)
    assert "Diagnostic counterfactual only" in bundle
    assert "independent fault clusters=2" in bundle
    assert "call#0" in bundle
    assert "call#1" in bundle


def test_resynchronization_exposes_two_independent_bad_writes():
    target = """
        sw zero,0(a0)
        sw zero,4(a0)
        jr ra
        nop
    """
    candidate = """
        li t0,1
        sw t0,0(a0)
        li t0,2
        sw t0,4(a0)
        jr ra
        nop
    """
    reports = differential.run_resynchronized_suite(
        target, candidate, (_case(),), max_divergences=3)
    report = reports[0]

    assert [row.kind for row in report.observations] == ["write", "write"]
    assert report.terminal_status == "passed"
    assert all(row.intervention_supported for row in report.observations)


def test_causal_slice_traces_different_call_argument_to_concrete_load():
    target = """
        addiu sp,sp,-8
        sw ra,4(sp)
        lhu a1,0(a0)
        jal observe
        move a0,a0
        lw ra,4(sp)
        jr ra
        addiu sp,sp,8
    """
    candidate = target.replace("lhu a1,0(a0)", "lhu a1,4(a0)")
    result = differential.run_suite(
        target, candidate,
        (_case(player_writes=((0, 2, 7), (4, 2, 9))),),
        call_arities={"observe": 2})[0]

    feedback = differential.causal_feedback(result, max_steps=5)

    assert "verified semantic-prefix contract" in feedback
    assert "! a1: target 0x00000007 <= i2 lhu player/2" in feedback
    assert "candidate 0x00000009 <= i2 lhu player+0x4/2" in feedback
    assert "executed target window" in feedback
    assert "lhu a1,0(a0)" in feedback
    assert "address from entry a0=player + 0x0" in feedback


def test_missing_call_exposes_both_branch_histories_beyond_short_window():
    target = '''
        addiu sp,sp,-8
        sw ra,4(sp)
        lbu t0,0(a0)
        beqz t0,38
        nop
        nop
        nop
        nop
        nop
        jal observe
        nop
        nop
        nop
        nop
        lw ra,4(sp)
        jr ra
        addiu sp,sp,8
    '''
    candidate=target.replace('lbu t0,0(a0)','lbu t0,1(a0)')
    case=_case(player_writes=((0,2,0x100),))
    result=differential.run_suite(target,candidate,(case,),call_arities={'observe':0})[0]
    assert result.target.status == result.candidate.status == 'returned'
    assert len(result.target.calls)==1 and len(result.candidate.calls)==0
    for row,missing_side in ((result,'candidate'),
            (differential.run_suite(candidate,target,(case,),call_arities={'observe':0})[0],'target')):
        feedback=differential.causal_slice(row,max_steps=2)
        assert 'not aligned branches' in feedback
        assert 'target branch decisions' in feedback and 'candidate branch decisions' in feedback
        assert 'player/1 -> 0x00000001' in feedback
        assert 'player+0x1/1 -> 0x00000000' in feedback
        assert 'branch taken;' in feedback and 'branch not taken;' in feedback
        assert missing_side+' terminal comparison window (returned)' in feedback


def test_causal_slice_traces_persistent_write_address_and_value():
    target = """
        lw t0,0(a0)
        addiu t0,t0,1
        sw t0,4(a0)
        jr ra
        nop
    """
    candidate = target.replace("sw t0,4(a0)", "sw t0,8(a0)")
    result = differential.run_suite(
        target, candidate, (_case(player_writes=((0, 4, 10),)),))[0]

    evidence = differential.causal_slice(result, max_steps=4)

    assert "replacement block: target writes [0:1]" in evidence
    assert "target: player+0x4/4=0xb at i2" in evidence
    assert "candidate: player+0x8/4=0xb at i2" in evidence
    assert "value provenance: i1 addiu" in evidence
    assert "executed candidate window ending at the store" in evidence


def test_causal_slice_identifies_extra_candidate_write_by_alignment():
    target = """
        sw zero,0(a0)
        sw zero,8(a0)
        jr ra
        nop
    """
    candidate = """
        sw zero,0(a0)
        sw zero,4(a0)
        sw zero,8(a0)
        jr ra
        nop
    """
    result = differential.run_suite(target, candidate, (_case(),))[0]

    evidence = differential.causal_slice(result, max_steps=3)

    assert "extra candidate write #1 before target write #1" in evidence
    assert "target: <none>" in evidence
    assert "candidate: player+0x4/4=0x0" in evidence


def test_mode16_sound_discriminators_reach_timer_branch_from_zero():
    cases = {case.name: case for case in differential.mode16_cases()}
    symbols = differential.SymbolTable({"gFrameCounter"})

    disabled = differential._seed_memory(
        symbols, cases["sound-disabled-with-state-bit-clear"])
    enabled = differential._seed_memory(
        symbols, cases["sound-enabled-with-state-bit-set"])

    assert disabled.read(differential.PLAYER_BASE + 0x7C, 4) == 0
    assert disabled.read(differential.PLAYER_BASE + 0x14, 1) == 1
    assert disabled.read(differential.PLAYER_BASE + 0x2FC, 4) & 1 == 0
    assert enabled.read(differential.PLAYER_BASE + 0x7C, 4) == 0
    assert enabled.read(differential.PLAYER_BASE + 0x14, 1) == 0
    assert enabled.read(differential.PLAYER_BASE + 0x2FC, 4) & 1 == 1


def test_entry_register_overrides_support_scalar_argument_paths():
    assembly = """
        sw a1,0(a0)
        jr ra
        nop
    """
    case = differential.TestCase(
        "register", 7, entry_registers=(("a1", 0x12345678),))
    result = differential.run_suite(assembly, assembly, (case,))[0]

    assert result.status == "passed"
    assert result.target.writes[0].value == 0x12345678


def test_coverage_report_keeps_unseen_branch_outcomes_unresolved():
    assembly = """
        lw t0,0(a0)
        beqz t0,done
        nop
        sw zero,4(a0)
    done:
        jr ra
        nop
    """
    program = differential.Program.parse("coverage", assembly)
    case = differential.TestCase(
        "not-taken", 1, player_writes=((0, 4, 1),))
    run = differential.execute_case(program, case)
    report = differential.coverage_report(program, (run,))

    assert report.instruction_complete
    assert not report.branch_edge_complete
    assert report.unresolved_branch_edges == ((1, True),)
    assert report.to_dict()["status"] == "partial"


def test_coverage_explorer_reaches_both_sides_of_nested_branches():
    assembly = """
        lw t0,0(a0)
        beqz t0,done
        nop
        lw t1,4(a0)
        beqz t1,done
        nop
        sw zero,8(a0)
    done:
        jr ra
        nop
    """
    seeds = (differential.TestCase(
        "both-nonzero", 1,
        player_writes=((0, 4, 1), (4, 4, 1))),)

    explored = differential.explore_coverage(
        assembly, seeds, max_cases=500)

    assert explored.report.complete
    assert explored.report.covered_branch_edges == (
        (1, False), (1, True), (4, False), (4, True))
    assert len(explored.cases) == 3
    assert explored.stop_reason.startswith("all reachable")


def test_coverage_proves_negative_division_fixup_unreachable_for_counter():
    assembly = """
        move s0,zero
    loop:
        bgez s0,rounded
        sra t0,s0,2
        addiu at,s0,3
        sra t0,at,2
    rounded:
        addiu s0,s0,1
        slti at,s0,4
        bnez at,loop
        nop
        jr ra
        nop
    """
    program = differential.Program.parse("division-loop", assembly)
    run = differential.execute_case(program, _case())

    report = differential.coverage_report(program, (run,))

    assert report.complete
    assert report.infeasible_branch_edges == ((1, False),)
    assert 3 not in report.reachable_instructions
    assert 4 not in report.reachable_instructions


def test_coverage_proves_ido_constant_divisor_guards_unreachable():
    assembly = """
        li a3,0x6400
        div v1,a3
        li a1,60
        bnez a3,nonzero
        nop
        break 7
    nonzero:
        li at,-1
        bne a3,at,safe
        nop
        bne v1,at,safe
        lui at,0x8000
        beq v1,at,safe
        nop
        break 6
    safe:
        mflo v0
        jr ra
        nop
    """
    program = differential.Program.parse("constant-divisor", assembly)
    run = differential.execute_case(program, _case())

    report = differential.coverage_report(program, (run,))

    assert run.status == "returned"
    assert report.complete
    assert (3, False) in report.infeasible_branch_edges
    assert (7, False) in report.infeasible_branch_edges
    assert 5 not in report.reachable_instructions
    assert 9 not in report.reachable_instructions
    assert 13 not in report.reachable_instructions


def test_coverage_explorer_can_mutate_scalar_a0():
    assembly = """
        beqz a0,zero_case
        nop
        li v0,1
        b done
        nop
    zero_case:
        li v0,2
    done:
        jr ra
        nop
    """
    seed = differential.TestCase(
        "nonzero", 1, entry_registers=(("a0", 1),))

    explored = differential.explore_coverage(
        assembly, (seed,), return_registers=("v0",),
        mutable_entry_registers=("a0",), max_cases=100)

    assert explored.report.complete
    assert any(dict(case.entry_registers).get("a0") == 0
               for case in explored.cases)


def test_semantic_stress_keeps_same_path_value_mutations():
    assembly = """
        lw t0,0(a0)
        addu v0,t0,a1
        jr ra
        nop
    """
    seed = differential.TestCase(
        "seed", 1, player_writes=((0, 4, 5),),
        entry_registers=(("a1", 7),))

    panel = differential.build_semantic_stress_panel(
        assembly, (seed,), return_registers=("v0",),
        mutable_entry_registers=("a1",), max_cases=12)

    dimensions = dict(panel.dimension_counts)
    assert dimensions["player+0x0/4"] > 0
    assert dimensions["register:a1"] > 0
    assert dimensions["seed"] > 0
    # There are no branches, so none of these mutations could have survived a
    # coverage-novelty filter.  They are retained to discriminate values.
    assert len(panel.cases) == 12
    assert all(run.status == "returned" for run in panel.runs)


def test_semantic_stress_includes_pointer_aliases_to_target_writes():
    assembly = """\
lbu t0,0(a1)
sh zero,0xc2(a0)
jr ra
sh t0,0xc4(a0)
"""
    seed = differential.TestCase(
        "base", 7,
        entry_registers=(("a1", differential.ARG_POINTER_BASES["a1"]),))

    panel = differential.build_semantic_stress_panel(
        assembly, (seed,), mutable_entry_registers=(),
        pointer_entry_registers=("a1",), max_cases=16)

    assert any(dict(case.entry_registers).get("a1") ==
               differential.PLAYER_BASE + 0xC2 for case in panel.cases)


def test_semantic_stress_rejects_target_faulting_mutations():
    assembly = """
        lui t0,%hi(gGate)
        lbu t0,%lo(gGate)(t0)
        bnez t0,done
        nop
        lw v0,0(a1)
    done:
        jr ra
        nop
    """
    seed = differential.TestCase(
        "valid-gated", 1, global_writes=(("gGate", 1, 1),))

    panel = differential.build_semantic_stress_panel(
        assembly, (seed,), mutable_entry_registers=(), max_cases=8)

    assert all(run.status == "returned" for run in panel.runs)
    assert dict(panel.rejected_status_counts)["memory_fault"] >= 1
    assert all(("gGate", 1, 0) not in case.global_writes
               for case in panel.cases)


def test_semantic_stress_replaces_target_faulting_seed_cases():
    assembly = """
        lw v0,0(a1)
        jr ra
        nop
    """
    seeds = (
        differential.TestCase("invalid", 1),
        differential.TestCase(
            "valid", 2, entry_registers=(("a1", differential.PLAYER_BASE),)),
    )

    panel = differential.build_semantic_stress_panel(
        assembly, seeds, mutable_entry_registers=("a1",), max_cases=6)

    assert all(run.status == "returned" for run in panel.runs)
    assert all(case.name != "invalid" for case in panel.cases)
    assert dict(panel.rejected_status_counts)["memory_fault"] >= 1


def test_semantic_stress_round_robins_across_seed_cases():
    assembly = """
        lh v0,0(a0)
        jr ra
        nop
    """
    seeds = tuple(
        differential.TestCase(
            f"seed-{index}", index + 1,
            player_writes=((0, 2, index + 3),))
        for index in range(3)
    )

    panel = differential.build_semantic_stress_panel(
        assembly, seeds, return_registers=("v0",),
        mutable_entry_registers=(), max_cases=9)

    mutated_roots = {case.name.split("-p", 1)[0]
                     for case in panel.cases[len(seeds):]
                     if "-p" in case.name}
    assert mutated_roots == {"seed-0", "seed-1", "seed-2"}


def test_terminal_return_accepts_normalizer_trimmed_nop_delay_slot():
    result = differential.run_suite(
        "li v0,7\njr ra", "li v0,7\njr ra",
        (_case(),), return_registers=("v0",))[0]

    assert result.status == "passed"
    assert result.target.status == "returned"
    assert result.target.trace[-1].effect == \
        "implicit terminal nop trimmed by normalizer"


def test_indirect_callback_is_hooked_but_keeps_concrete_identity():
    assembly = """
        addiu sp,sp,-0x18
        sw ra,0x14(sp)
        lui t0,%hi(callback_slot)
        lw t9,%lo(callback_slot)(t0)
        jalr t9
        nop
        li v0,7
        lw ra,0x14(sp)
        addiu sp,sp,0x18
        jr ra
        nop
    """

    result = differential.run_suite(
        assembly, assembly, (_case(),), return_registers=("v0",))[0]

    assert result.status == "passed"
    assert result.target.status == "returned"
    assert len(result.target.calls) == 1
    assert result.target.calls[0].callee.startswith("indirect@")
    assert result.target.calls[0].raw_arguments == ()
    assert result.target.return_values["v0"] == 7


def test_indirect_callback_target_difference_is_observable():
    target = """
        addiu sp,sp,-0x18
        sw ra,0x14(sp)
        lui t0,%hi(callback_slot)
        lw t9,%lo(callback_slot)(t0)
        jalr t9
        nop
        lw ra,0x14(sp)
        addiu sp,sp,0x18
        jr ra
        nop
    """
    candidate = target.replace("callback_slot", "other_callback_slot")

    result = differential.run_suite(target, candidate, (_case(),))[0]

    assert result.status == "failed"
    assert any("external call trace" in reason for reason in result.reasons)


def test_integer_multiply_divide_and_register_compare():
    assembly = """
        li t0,-7
        li t1,3
        mult t0,t1
        mflo t2
        div zero,t2,t1
        mflo v0
        slt t3,v0,zero
        sw t3,0(a0)
        jr ra
        nop
    """
    result = differential.run_suite(
        assembly, assembly, (_case(),), return_registers=("v0",))[0]

    assert result.status == "passed"
    assert result.target.return_values["v0"] == differential.u32(-7)
    assert result.target.writes[0].value == 1


def test_single_precision_convert_arithmetic_and_fcsr_truncation():
    assembly = """
        li t0,7
        mtc1 t0,$f0
        cvt.s.w $f0,$f0
        li t1,2
        mtc1 t1,$f2
        cvt.s.w $f2,$f2
        div.s $f4,$f0,$f2
        li t2,1
        ctc1 t2,c1_fcsr
        cvt.w.s $f6,$f4
        mfc1 v0,$f6
        jr ra
        nop
    """

    result = differential.run_suite(
        assembly, assembly, (_case(),), return_registers=("v0",))[0]

    assert result.status == "passed"
    assert result.target.return_values["v0"] == 3


def test_cvt_w_s_unsupported_inputs_are_inconclusive_for_all_rounding_modes():
    assembly = "cvt.w.s f6,f4\nmfc1 v0,f6\njr ra\nnop"
    program = differential.Program.parse("convert", assembly)
    # Infinity, NaN, and finite values beyond signed word range all require
    # conversion exception/FCSR behavior that the runner does not model.
    for bits in (0x7F800000, 0xFF800000, 0x7FC00000,
                 0x4F000000, 0xCF000001):
        for rounding in range(4):
            case = _case(entry_registers=(("f4", bits),
                                          ("c1_fcsr", rounding)))
            run = differential.execute_case(program, case,
                                            return_registers=("v0",))
            assert run.status == "unsupported", (bits, rounding, run)
            assert "cvt.w.s" in run.error

    result = differential.compare_programs(
        program, program,
        _case(entry_registers=(("f4", 0x7F800000), ("c1_fcsr", 1))))
    assert result.status == "inconclusive"

    explored = differential.explore_coverage(
        assembly,
        (_case(entry_registers=(("f4", 0x7F800000),
                                ("c1_fcsr", 1))),),
        max_cases=1)
    assert explored.trial_status_counts == {"unsupported": 1}
    assert explored.execution_obstructions[0]["status"] == "unsupported"


def test_cvt_w_s_keeps_finite_in_range_rounding_behavior():
    program = differential.Program.parse(
        "convert", "cvt.w.s f6,f4\nmfc1 v0,f6\njr ra\nnop")
    for bits, expected in (
        (0x3FC00000, (2, 1, 2, 1)),
        (0xBFC00000, (-2, -1, -1, -2)),
        (0x4EFFFFFF, (2147483520,) * 4),
        (0xCF000000, (-2147483648,) * 4),
    ):
        for rounding, converted in enumerate(expected):
            case = _case(entry_registers=(("f4", bits),
                                          ("c1_fcsr", rounding)))
            run = differential.execute_case(program, case,
                                            return_registers=("v0",))
            assert run.status == "returned", (bits, rounding, run)
            assert run.return_values["v0"] == converted & 0xFFFFFFFF


def test_d_hex_absolute_symbol_resolves_to_its_linker_value():
    target = """
        lui v0,%hi(D_3FFFF)
        addiu v0,v0,%lo(D_3FFFF)
        jr ra
        nop
    """
    candidate = """
        lui v0,0x4
        addiu v0,v0,-1
        jr ra
        nop
    """

    result = differential.run_suite(
        target, candidate, (_case(),), return_registers=("v0",))[0]

    assert result.status == "passed"
    assert result.target.return_values["v0"] == 0x3FFFF


def test_dot_prefixed_section_symbol_relocation_executes():
    assembly = """
        lui v0,%hi(.rodata)
        addiu v0,v0,%lo(.rodata)
        jr ra
        nop
    """

    result = differential.run_suite(
        assembly, assembly, (_case(),), return_registers=("v0",))[0]

    assert result.status == "passed"
    assert result.target.return_values["v0"] != 0


def test_sine_table_region_accepts_full_twelve_bit_index_span():
    assembly = """
        lui t0,%hi(gSineTable)
        addiu t0,t0,%lo(gSineTable)
        addiu t0,t0,0x1ffc
        lh v0,0(t0)
        jr ra
        nop
    """
    result = differential.run_suite(
        assembly, assembly, (_case(),), return_registers=("v0",))[0]

    assert result.status == "passed"


def test_memory_fault_trace_names_instruction_and_address_provenance():
    target = """
        jr ra
        nop
    """
    candidate = """
        move v0,zero
        sb zero,8(v0)
        jr ra
        nop
    """

    result = differential.run_suite(
        target, candidate, (_case(),))[0]
    evidence = differential.causal_slice(result, max_steps=4)

    assert result.candidate.status == "memory_fault"
    assert "candidate terminal/fault window" in evidence
    assert "target returned comparison window" in evidence
    assert "jr ra" in evidence.split("candidate terminal/fault window")[0]
    assert "sb zero,8(v0)" in evidence
    assert "effective address from constant 0 + 0x8" in evidence
    assert "later missing calls are consequences" in evidence
    reverse = differential.run_suite(candidate,target,(_case(),))[0]
    reverse_evidence = differential.causal_slice(reverse,max_steps=4)
    assert "target terminal/fault window" in reverse_evidence
    assert "candidate returned comparison window" in reverse_evidence

    report = differential.run_resynchronized_suite(
        target, candidate, (_case(),))[0]
    assert [row.kind for row in report.observations] == ["terminal"]
    assert "memory_fault" in report.observations[0].signature
