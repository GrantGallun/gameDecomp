from pathlib import Path
from solver.callback_dispatch_alternatives import candidates

NAME='createCallbackTaskPreservingArgs'
def seed():
    return (Path(__file__).resolve().parents[1]/f'eval/results/swarm-final/{NAME}.c').read_text()

def test_bounded_unique_and_scoped():
    src=seed()+'\nint other(void) { return 3; }\n'
    result=candidates(src,NAME,60)
    assert len(result)==len({v.source for v in result})
    assert len(candidates(src,NAME,3))==3
    assert not candidates(src,NAME,0)
    assert not candidates(src,'other')
    assert all(v.source.endswith('int other(void) { return 3; }\n') for v in result)

def test_inline_index_keeps_decrement_before_load():
    variant=next(v for v in candidates(seed(),NAME) if v.label=='dispatch:inline-index')
    assert 'u16 idx;' not in variant.source
    assert 'gFreeCallbackTaskPool[gFreeCallbackTaskCount]' in variant.source
    assert variant.source.index('gFreeCallbackTaskCount--;') < variant.source.index('newTask = gFreeCallbackTaskPool[')

def test_unknown_or_effect_qualified_shapes_rejected():
    assert not candidates('int createCallbackTaskPreservingArgs(void) { return 0; }',NAME)
    assert not candidates(seed().replace('u16 idx;','volatile u16 idx;'),NAME)
