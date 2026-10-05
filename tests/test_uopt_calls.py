"""Tests for mapping uopt's flow graph onto ugen's u-code and finding the calls live ranges cross.

Fixtures are the real `-zdbug:5` flow graph and `-Wc,-d` Build tree of SBK1's
updateCallbackTasks (a do-while calling through a pointer): uopt splits the loop body
at the call, prints its flow graph out of program order and has an empty node on the
loop exit. Each of those shapes has a test that the matcher FIRES on, and each way it
must decline has one too.
"""

from solver import uopt_calls as C
from solver import uopt_trace as U

LEVEL5 = """\
   flow graph for updateCallbackTasks:
           0           0
suc::::           1           0
suc::::           5          22
           1           0
pre::::           0           0
suc::::           2          20
           2          20
pre::::           3           0
pre::::           1           0
suc::::           3           0
           3           0
pre::::           2          20
suc::::           4           0
suc::::           2          20
           4           0
pre::::           3           0
suc::::           5          22
           5          22
pre::::           4           0
pre::::           0           0
 * *    0.  5 SECONDS IN global coloring of updateCallbackTasks
>>>active<<<{1329|0}           4          14
adjsave, hasstore: 3.10000000e+01  true
forbidden: []
:::interfere with:::   17   16    2
- live bb -   0  1  1  0
firstisstr deadout needreglod needregsave   true false false false
- live bb -   2  3  1  3
firstisstr deadout needreglod needregsave  false  true false false
- live bb -   3  1  1  0
firstisstr deadout needreglod needregsave   true false false false
- live bb (default) (  1) [   1]
% % % node   2 loopdepth            2loopfirstbb
% % % node   3 loopdepth            2notloopfirstbb
"""

DUMP = """\
Tree dump after Build

   238\t  ent dtype=P lexlev=2 blockno=23 push=0 pop=0 external=1 Not visited next=304 prior=237
   304\t  def mtype=T length=16 Not visited next=302
   246\t  str dtype=A mtype=R lexlev=0 blockno=3 length=4 offset=68 Not visited op1=245 next=247 prior=244
   245\t   lda mtype=S blockno=25 length=4 offset=0 offset2=19 Not visited
   261\t  fjp i1=22 Not visited op1=259 op2=260 next=262 prior=256
   262\t  loc lexlev=2 blockno=107 Not visited next=263 prior=261
   264\t  lab lexlev=0 i1=20 length=0 Not visited next=265 prior=263
   272\t  mst lexlev=0 Not visited next=275 prior=271
   278\t  rpar dtype=A mtype=P lexlev=4 blockno=0 length=4 offset=0 Not visited next=281 prior=277
   281\t  icuf dtype=P push=0 pop=1 Not visited op1=280 next=282 prior=278
   282\t  loc lexlev=2 blockno=112 Not visited next=286 prior=281
   293\t  str dtype=A mtype=R lexlev=0 blockno=0 length=4 offset=64 Not visited op1=292 next=297 prior=290
   297\t  tjp i1=20 Not visited op1=296 op2=264 next=298 prior=293
   296\t   neq dtype=A Not visited op1=295 op2=294
   298\t  foo Not visited next=260 prior=297
   260\t  lab lexlev=0 i1=22 length=0 Not visited next=299 prior=298
   300\t  ujp i1=0 Not visited op2=239 next=239 prior=299
   239\t  lab lexlev=0 i1=42 length=0 Not visited next=303 prior=300
   303\t  end Not visited prior=239
Tree dump after Translate

   238\t  ent dtype=P lexlev=2 blockno=23 push=0 pop=0 external=1 next=304 prior=237
"""


def _graph_and_blocks():
    graph = C.flow_graphs(LEVEL5)["updateCallbackTasks"]
    (statements,) = C.ugen_procedures(DUMP)
    return graph, C.blocks_of(statements)


def test_only_the_build_section_and_only_top_level_statements_are_read():
    (statements,) = C.ugen_procedures(DUMP)
    ops = [op for op, _ in statements]
    assert "lda" not in ops and "neq" not in ops            # children are indented further
    assert ops.count("ent") == 1                            # the Translate section is ignored


def test_blocks_split_after_calls_as_well_as_at_labels_and_jumps():
    _, blocks = _graph_and_blocks()
    assert [b.label for b in blocks] == [0, 0, 20, 0, 0, 22, 42]
    assert [b.ends_call for b in blocks] == [False, False, True, False, False, False, False]
    assert blocks[3].successors == [4, 2]                   # tjp 20: fall-through, then the loop head


def test_flow_graph_parses_nodes_labels_and_successors():
    graph = C.flow_graphs(LEVEL5)["updateCallbackTasks"]
    assert graph[0] == (0, [1, 5]) and graph[3] == (0, [4, 2]) and graph[5] == (22, [])
    assert C.loop_depths(LEVEL5)["updateCallbackTasks"] == {2: 2, 3: 2}


def test_match_pairs_every_node_and_finds_the_call_node():
    graph, blocks = _graph_and_blocks()
    pairs = C.match(graph, blocks)
    assert pairs == {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5}
    assert C.call_nodes(graph, blocks) == [2]


def test_uopt_only_empty_node_is_matched_as_a_pass_through():
    graph = C.flow_graphs(LEVEL5)["updateCallbackTasks"]
    without_foo = "\n".join(line for line in DUMP.splitlines() if "  foo " not in line)
    (statements,) = C.ugen_procedures(without_foo)
    blocks = C.blocks_of(statements)                        # the tjp now falls straight into lab 22
    pairs = C.match(graph, blocks)
    assert pairs is not None and pairs[4] is None and pairs[5] == 4


def test_match_declines_when_a_labelled_successor_is_missing_or_ambiguous():
    graph, blocks = _graph_and_blocks()
    broken = dict(graph)
    broken[5] = (23, [])                                    # label 22 in the dump, 23 in the graph
    broken[0] = (0, [1, 5])
    assert C.match(broken, blocks) is None
    two_plain = dict(graph)
    two_plain[6] = (0, [])
    two_plain[0] = (0, [1, 6, 5])                           # two unlabelled successors: no guessing
    assert C.match(two_plain, blocks) is None


def test_block_flags_parse_and_crossing_needs_live_in_and_live_out():
    record = U.parse_level5(LEVEL5)["updateCallbackTasks"][4]
    assert record.block_flags[2] == (False, True, False, False)
    # live into the call node but dead out of it: not a crossing at node 2
    assert C.crossed_calls(record, [2]) == []
    record.block_flags[2] = (False, False, False, False)
    assert C.crossed_calls(record, [2]) == [2]
    record.block_flags[2] = (True, False, False, False)     # first occurrence is a store after the call
    assert C.crossed_calls(record, [2]) == []
    assert C.crossed_calls(record, [1]) == [1]              # live through a call node (default block)


def test_band_threshold_uses_loop_weighted_calls():
    record = U.LiveRange(lr=1, node=1, color=14, default_blocks=frozenset({2, 7}))
    assert C.call_weight([2], {2: 2}) == 10
    assert C.predict_band(record, [2], {2: 2}) == "int_callee"
    assert C.predict_band(record, [7], {7: 1}) == "int_caller"             # one call outside a loop
    assert C.predict_band(record, [2, 7], {2: 1, 7: 1}) == "int_caller"    # weight 2
    record.default_blocks = frozenset({2, 7, 9})
    assert C.predict_band(record, [2, 7, 9], {}) == "int_callee"           # weight 3
