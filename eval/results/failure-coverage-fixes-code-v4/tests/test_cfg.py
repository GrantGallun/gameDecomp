"""Control-flow graph recovery and graph-algorithm tests."""

from solver import cfg


DIAMOND = """\
glabel f
    beqz $a0, .Lelse
    nop
    addiu $v0, $zero, 1
    b .Ljoin
    nop
.Lelse:
    addiu $v0, $zero, 2
.Ljoin:
    jr $ra
    nop
"""


LOOP = """\
glabel loop
    move $t0, $zero
.Lhead:
    addiu $t0, $t0, 1
    bne $t0, $a0, .Lhead
    nop
    jr $ra
    nop
"""


def test_delay_slot_stays_with_terminating_branch():
    graph = cfg.build(DIAMOND)
    first = graph.blocks[graph.entry]
    assert first.terminator.opcode == "beqz"
    assert first.delay_slot is not None
    assert first.delay_slot.opcode == "nop"
    assert first.end == first.terminator.index + 1


def test_diamond_has_two_successors_and_one_join():
    graph = cfg.build(DIAMOND)
    first = graph.blocks[graph.entry]
    assert len(first.successors) == 2
    joins = [b.id for b in graph.blocks.values() if len(b.predecessors) == 2]
    assert len(joins) == 1


def test_dominators_and_postdominators_find_diamond_boundaries():
    graph = cfg.build(DIAMOND)
    entry = graph.entry
    join = next(b.id for b in graph.blocks.values()
                if len(b.predecessors) == 2)
    dom = graph.dominators()
    post = graph.postdominators()
    assert all(entry in values for values in dom.values())
    assert join in post[entry]
    assert graph.immediate_postdominators()[entry] == join


def test_backward_edge_produces_scc_and_natural_loop():
    graph = cfg.build(LOOP)
    cyclic = [s for s in graph.strongly_connected_components() if len(s) > 1]
    # A single-block self-loop is also a cycle; this fixture normally lowers
    # to one loop block, so accept either representation and check the edge.
    loops = graph.natural_loops()
    assert loops
    assert any(loop.header in graph.blocks[loop.latch].successors
               for loop in loops)
    assert cyclic or any(loop.header == loop.latch for loop in loops)


def test_reverse_postorder_covers_every_reachable_block_once():
    graph = cfg.build(DIAMOND)
    order = graph.reverse_postorder()
    assert order[0] == graph.entry
    assert len(order) == len(set(order)) == len(graph.reachable())


def test_unresolved_indirect_jump_is_explicit():
    graph = cfg.build("glabel f\n  jr $t9\n  nop\n")
    block = graph.blocks[graph.entry]
    assert block.unknown_successor
    assert block.successors == set()


def test_sese_candidates_are_conservative_and_include_diamond():
    graph = cfg.build(DIAMOND)
    join = next(b.id for b in graph.blocks.values()
                if len(b.predecessors) == 2)
    assert any(r.entry == graph.entry and r.exit == join
               for r in graph.sese_regions())

