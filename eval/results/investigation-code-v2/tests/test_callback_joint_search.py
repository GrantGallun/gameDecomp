from pathlib import Path
from solver.callback_joint_search import candidates

NAME='createCallbackTaskPreservingArgs'
def seed():
    return (Path(__file__).resolve().parents[1]/f'eval/results/callback-finish-final/{NAME}.c').read_text()

def test_joint_search_is_bounded_unique_and_function_scoped():
    source=seed()+'\nint untouched(void) { return 1; }\n'
    variants=candidates(source,NAME)
    assert 60<len(variants)<=96
    assert len({v.source for v in variants})==len(variants)
    assert len(candidates(source,NAME,3))==3
    assert not candidates(source,NAME,0)
    assert not candidates(source,'untouched')
    assert all(v.source.endswith('int untouched(void) { return 1; }\n') for v in variants)

def test_combinations_cross_lower_scoring_intermediates():
    variants=candidates(seed(),NAME)
    candidate=next(v for v in variants if v.label=='callback-joint:index-first:new-base:late-allocation')
    assert 'idx = gFreeCallbackTaskCount - 1;' in candidate.source
    assert 'head = &gCallbackTaskActiveListSentinel;' in candidate.source
    assert candidate.source.index('newTask = gFreeCallbackTaskPool[idx];') > candidate.source.index('if (cur == NULL) break;')
    assert candidate.source.index('newTask = gFreeCallbackTaskPool[idx];') < candidate.source.index('newTask->prev = insertAfter;')

def test_unknown_shape_or_volatile_source_rejected():
    assert not candidates(seed().replace('u16 idx;','volatile u16 idx;'),NAME)
    assert not candidates(seed().replace('cur = insertAfter->next;','cur = unknown();'),NAME)
