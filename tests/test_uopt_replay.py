"""Tests for the control-flow colouring replay that validates the uopt model.

This tool decides whether the allocator model is trustworthy enough to steer a
search. Every way it could quietly measure the wrong thing got a test that FIRES
on text containing that case: linear web fragmentation, a CFG built without its
numeric-branch marker, hex branch offsets misread as registers, float loads and
multiplies misread as writes, and ABI traffic or frame saves scored as
allocation decisions.
"""

from eval import uopt_replay as R
from solver import cfg


def _insn(text):
    return cfg.parse_assembly(text)[0][0]


def _components(asm):
    analysis = R.analyse(asm)
    assert isinstance(analysis, dict)
    return analysis, list(analysis["components"].values())


# ---- inputs: the graph and def/use decisions ---------------------------------

def test_numeric_branch_targets_resolve_into_real_edges():
    asm = "beqz    a0,10\nnop\nb    14\nli    t1,1\nli    t1,2\nsw    t1,0x0(a1)\njr    ra\nnop"
    graph = R.graph_for(asm)
    assert not any(b.unknown_successor for b in graph.blocks.values())
    assert any(len(b.successors) == 2 for b in graph.blocks.values())
    # Without the marker the same text has no resolvable branch at all.
    bare = cfg.build(asm)
    assert any(b.unknown_successor for b in bare.blocks.values())


def test_a_hex_branch_offset_is_not_read_as_an_argument_register():
    assert R.local_def_use(_insn("bnez    t2,a0")) == ([], ["t2"])


def test_float_load_writes_the_float_register_not_its_base():
    assert R.local_def_use(_insn("lwc1    f0,0x4(a0)")) == ([], ["a0"])


def test_multiply_writes_hi_lo_so_both_operands_are_reads():
    assert R.local_def_use(_insn("mult    a0,a1")) == ([], ["a0", "a1"])


def test_frame_save_and_restore_are_not_values():
    assert R.local_def_use(_insn("sw    s0,0x10(sp)")) == ([], [])
    assert R.local_def_use(_insn("lw    s0,0x10(sp)")) == ([], [])


# ---- webs follow control flow ----------------------------------------------

def test_definitions_on_both_arms_of_a_branch_form_one_web():
    """Fires on the bug that invalidated the linear version.

    t1 is set to 1 on one path and 2 on the other, then used after the join.
    Linear splitting made that two webs with empty ranges; it is one value.
    """
    asm = "\n".join([
        "beqz    a0,10",        # 0 -> 4 when a0 == 0
        "nop",                  # 1 delay slot
        "b    14",              # 2 -> 5
        "li    t1,1",           # 3 delay slot: this path's t1
        "li    t1,2",           # 4 the other path's t1
        "sw    t1,0x0(a1)",     # 5 join: one use of both definitions
        "jr    ra",
        "nop",
    ])
    _analysis, comps = _components(asm)
    t1 = [c for c in comps if c.register == "t1" and c.use_lines]
    assert len(t1) == 1
    assert sorted(t1[0].def_lines) == [3, 4] and t1[0].use_lines == [5]


CALL = "\n".join([
    "addiu    sp,sp,-0x20",     # 0
    "sw    ra,0x14(sp)",        # 1
    "sw    s0,0x10(sp)",        # 2  frame save
    "move    s0,a0",            # 3  s0 from the incoming parameter
    "li    a0,5",               # 4  argument for the call
    "jal    func_80001234",     # 5
    "nop",                      # 6
    "lw    t0,0x4(s0)",         # 7  s0 used after the call: it crossed it
    "addiu    v0,t0,1",         # 8  return value
    "lw    s0,0x10(sp)",        # 9  frame restore
    "lw    ra,0x14(sp)",        # 10
    "jr    ra",                 # 11
    "addiu    sp,sp,0x20",      # 12
])


def test_abi_webs_are_classified_and_real_allocation_is_scored():
    _analysis, comps = _components(CALL)
    by = {(c.register, tuple(sorted(c.def_lines))): R.classify(c) for c in comps if c.use_lines}
    assert by[("a0", ())] == "abi"          # incoming parameter (entry value)
    assert by[("a0", (4,))] == "abi"        # call argument
    assert by[("v0", (8,))] == "abi"        # return value at jr ra
    assert by[("s0", (3,))] == "scored"
    assert by[("t0", (7,))] == "scored"


def test_a_value_live_across_a_call_is_marked_crossing():
    _analysis, comps = _components(CALL)
    s0 = next(c for c in comps if c.register == "s0" and c.def_lines == [3])
    t0 = next(c for c in comps if c.register == "t0" and c.def_lines == [7])
    assert s0.crosses_call is True and t0.crosses_call is False


# ---- replay mechanics -----------------------------------------------------

def _webs(asm):
    analysis = R.analyse(asm)
    webs, _classes, crossing = R.as_webs(analysis, [i.text for i in analysis["insns"]])
    return analysis, webs, crossing


def test_interfering_values_get_distinct_registers_in_priority_order():
    asm = "li    t1,1\nli    t2,2\naddu    t3,t1,t2\nsw    t3,0x0(a0)\njr    ra\nnop"
    analysis, webs, crossing = _webs(asm)
    a = next(w for w in webs if w.register == "t1")
    b = next(w for w in webs if w.register == "t2")
    first = R.replay(webs, analysis["adjacency"], crossing, [a, b])
    assert (first[a.number], first[b.number]) == ("v0", "v1")
    second = R.replay(webs, analysis["adjacency"], crossing, [b, a])
    assert (second[b.number], second[a.number]) == ("v0", "v1")


def test_values_that_never_coexist_may_share_the_lowest_register():
    asm = "li    t1,1\nsw    t1,0x0(a0)\nli    t2,2\nsw    t2,0x4(a0)\njr    ra\nnop"
    analysis, webs, crossing = _webs(asm)
    got = R.replay(webs, analysis["adjacency"], crossing, webs)
    assert {got[w.number] for w in webs if not w.precolored} == {"v0"}


def test_a_crossing_value_is_replayed_into_a_callee_saved_register():
    analysis, webs, crossing = _webs(CALL)
    got = R.replay(webs, analysis["adjacency"], crossing, webs)
    s0 = next(w for w in webs if w.register == "s0")
    assert got[s0.number] in R.CALLEE


# ---- evaluate ----------------------------------------------------------------

def test_evaluate_fires_and_reports_every_control():
    result = R.evaluate(CALL, seeds=3)
    assert isinstance(result, dict)
    for key in ("webs", "model", "oracle", "chrono", "random", "crossing",
                "crossing_in_callee", "single_occurrence", "oracle_lower"):
        assert key in result
    assert result["webs"] == 2 and result["crossing_in_callee"] == 1


def test_evaluate_declines_when_nothing_is_an_allocation_decision():
    assert R.evaluate("move    v0,a0\njr    ra\nnop") is None


def test_a_jump_table_is_reported_unresolved_not_guessed():
    assert R.evaluate("lw    t6,0x0(a0)\njr    t6\nnop") == "unresolved"
