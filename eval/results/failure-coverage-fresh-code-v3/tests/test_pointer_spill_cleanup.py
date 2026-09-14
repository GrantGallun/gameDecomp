import pytest
from solver import pointer_spill_cleanup as cleanup


SOURCE='''void f(void) {
    u8 *spill;
    u8 *bytes;
    Camera *camera;
    spill = bytes;
    spill = camera;
    use(bytes, camera);
}
'''


def test_removes_only_unused_pointer_copies():
    r=cleanup.propose(SOURCE,'f')
    assert len(r['changes'])==1 and r['changes'][0]['local']=='spill'
    assert 'spill' not in r['source']
    assert 'use(bytes, camera);' in r['source']
    assert not cleanup.propose(r['source'],'f')['changes']


@pytest.mark.parametrize('source',[
    SOURCE.replace('spill = bytes;', 'spill = getPointer();'),
    SOURCE.replace('spill = bytes;', 'spill = bytes++;'),
    SOURCE.replace('spill = bytes;', 'spill = *bytes;'),
    SOURCE.replace('use(bytes, camera);', 'use(spill);'),
    SOURCE.replace('use(bytes, camera);', 'use(&spill);'),
    SOURCE.replace('u8 *bytes;', 'u8 * volatile bytes;'),
    SOURCE.replace('u8 *spill;', 'u8 * volatile spill;'),
    SOURCE.replace('u8 *bytes;', ''),
    SOURCE.replace('spill = bytes;', 'if (flag) spill = bytes;')])
def test_declines_effects_reads_escapes_and_unsupported_declarations(source):
    assert not cleanup.propose(source,'f')['changes']


def test_preserves_other_functions_and_comments():
    tail='\nvoid g(void) { use(spill); }\n/* spill = bytes; */'
    assert cleanup.propose(SOURCE+tail,'f')['source'].endswith(tail)
