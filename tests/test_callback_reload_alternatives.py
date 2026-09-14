from pathlib import Path
from solver.callback_reload_alternatives import candidates

ROOT=Path(__file__).resolve().parents[1]
FUNCTION='createCallbackTaskPreservingArgs'
SOURCE=(ROOT/'eval/results/swarm-final/createCallbackTaskPreservingArgs.c').read_text()

def test_first_candidate_reproduces_semantically_replayed_reload():
    v=candidates(SOURCE,FUNCTION,1)[0]
    expected=(ROOT/'eval/results/callback-finish-reload-v1/10.c').read_text()
    assert v.source==expected
    assert 'volatile' not in v.source
    assert 'for (;;)' in v.source
    assert 'do {' not in v.source

def test_budget_scope_and_unrecognized_loop_guards():
    assert not candidates(SOURCE,FUNCTION,0)
    assert not candidates(SOURCE,'unrelated')
    assert not candidates(SOURCE.replace('cur = cur->next;','touch(cur); cur = cur->next;'),FUNCTION)
    variants=candidates(SOURCE,FUNCTION,9)
    assert len(variants)==9
    assert len({v.source for v in variants})==9
    prefix=SOURCE.split('    cur = insertAfter->next;')[0]
    suffix=SOURCE.split('    /* Insert newTask after insertAfter */')[1]
    assert all(v.source.startswith(prefix) for v in variants)
    assert all(v.source.endswith(suffix) for v in variants)


def test_declines_unknown_head_base_or_intervening_effect():
    assert not candidates(SOURCE.replace('insertAfter = &gCallbackTaskActiveListSentinel;',
                                        'insertAfter = differentList;'),FUNCTION)
    assert not candidates(SOURCE.replace('newTask = gFreeCallbackTaskPool[idx];',
                                        'newTask = getNodeAndMutateHead();'),FUNCTION)
