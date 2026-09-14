from solver import wide_return_repair as w


SOURCE='''s32 multiply(s32, s32, s32, s32);
s32 f(s32 a) {
    s32 result;
    s32 hi;
    u32 lo;
    result = multiply(0,a,0,a);
    hi = result;
    lo = (u32) (u64) result;
    return (hi - result) - lo;
}
'''


def setup(monkeypatch):
    monkeypatch.setattr(w.callee_execution,'source_contracts',lambda s,e:[{
        'callee':'multiply','status':'result-width-conflict'}])


def test_fires_on_closed_word_pair_uses(monkeypatch):
    setup(monkeypatch)
    r=w.propose(SOURCE,'f',None,byteorder='big')
    assert r['changes']
    assert 'u64 multiply(s32, s32, s32, s32);' in r['source']
    assert 'hi = result.words.high;' in r['source']
    assert 'lo = result.words.low;' in r['source']
    assert 'result.wide = multiply' in r['source']


def test_declines_nonclosed_or_shared_shapes(monkeypatch):
    setup(monkeypatch)
    variants=[SOURCE.replace('s32 result;','volatile s32 result;'),
        SOURCE.replace('hi = result;','hi = result + 1;'),
        SOURCE+'s32 g(void) { return multiply(0,1,0,1); }',
        SOURCE.replace('hi = result;','result = 3; hi = result;'),
        SOURCE.replace('lo = (u32) (u64) result;','lo = result;')]
    for source in variants:
        r=w.propose(source,'f',None,byteorder='big')
        assert not r['changes']
        assert r['declines']
        assert r['source']==source
    assert not w.propose(SOURCE,'f',None,byteorder='little')['changes']


def test_requires_admitted_contract(monkeypatch):
    monkeypatch.setattr(w.callee_execution,'source_contracts',lambda s,e:[])
    assert not w.propose(SOURCE,'f',None,byteorder='big')['changes']
