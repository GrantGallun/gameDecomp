from types import SimpleNamespace
from solver import semantic_gradient as g


def test_long_dynamic_value_chain_is_explicitly_truncated_not_a_recursion_crash():
    trace = [SimpleNamespace(ordinal=i,instruction=i,text='addiu t0,t0,1',
        writes=(('t0',i,''),),reads=()) for i in range(3000)]
    trace.append(SimpleNamespace(ordinal=3000,instruction=3000,text='sw t0,0(a0)',writes=(),reads=()))
    node = g.value_dag(SimpleNamespace(trace=trace),SimpleNamespace(trace_position=3000,address='player'))
    for _ in range(64):
        assert node.kind == 'operation'
        node = node.children[0]
    assert node.kind == 'unknown' and node.op == 'diagnostic-traversal-budget'
