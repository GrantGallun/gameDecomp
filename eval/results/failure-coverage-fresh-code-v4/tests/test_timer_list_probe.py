"""Guard the controlled experiment's inputs, not a general layout inference."""
import importlib

from solver import mips_differential as d


def test_timer_probe_covers_word_boundaries_and_closed_one_node_links():
    module=importlib.import_module('eval.experiments.campaign-gap-audit.timer_list_probe')
    cases=module.cases()
    assert len(cases)==37 and len({case.name for case in cases})==37
    values=set()
    intervals=set()
    messages=set()
    for case in cases[:-1]:
        globals_={name:value for name,width,value in case.global_writes}
        head={offset:value for offset,width,value in case.player_writes}
        assert globals_['__osTimerList']==d.PLAYER_BASE
        assert head[0]==head[4]==d.ARG_POINTER_BASES['a1']
        assert globals_['@arg1+0x0']==globals_['@arg1+0x4']==d.PLAYER_BASE
        values.add((globals_['@arg1+0x10'],globals_['@arg1+0x14']))
        intervals.add((globals_['@arg1+0x8'],globals_['@arg1+0xc']))
        messages.add(globals_['@arg1+0x18'])
        assert case.call_returns==(('osGetCount',0,20),)
    assert {(0,19),(0,20),(0,21),(1,0)}<=values
    assert intervals=={(0,0),(0,1),(1,0)}
    assert messages=={0,d.ARG_POINTER_BASES['a2']}
    assert cases[-1].player_writes==((0,4,d.PLAYER_BASE),(4,4,d.PLAYER_BASE))
