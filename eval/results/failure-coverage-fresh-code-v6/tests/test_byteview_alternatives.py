import pytest
from solver import compile_recovery as recovery


def test_incomplete_draft_kept_alongside_header_alternative(monkeypatch):
    calls=[]
    def draft(*args, **kwargs):
        calls.append(kwargs)
        return ('void f(void) { M2C_UNK x; }', {'draft':{}}) if not kwargs else (
            'void f(void) { int x; }', {'draft':{'headers':['public.h']}})
    monkeypatch.setattr(recovery,'byteview_redraft',draft)
    rows=list(recovery.byteview_redrafts(None,None,'f','#include "public.h"\n'))
    assert len(rows)==2 and 'M2C_UNK' in rows[0][1]
    assert rows[1][0]=='header-byteview-alternative'
    assert calls==[{}, {'header_first':True}]


@pytest.mark.parametrize('source,meta', [
    ('void f(void) {} /* M2C_UNK */', {}),
    ('void f(void) { M2C_UNK x; }', {'headers':['public.h']})])
def test_no_duplicate_or_comment_trigger(monkeypatch,source,meta):
    calls=[]
    def draft(*args,**kwargs):
        calls.append(kwargs)
        return source,{'draft':meta}
    monkeypatch.setattr(recovery,'byteview_redraft',draft)
    assert len(list(recovery.byteview_redrafts(None,None,'f','#include "public.h"\n')))==1
    assert calls==[{}]


def test_failed_alternative_does_not_discard_yielded_candidate(monkeypatch):
    def draft(*args,**kwargs):
        if kwargs:raise ValueError('header ABI mismatch')
        return 'void f(void) { M2C_UNK x; }', {'draft':{}}
    monkeypatch.setattr(recovery,'byteview_redraft',draft)
    candidates=recovery.byteview_redrafts(None,None,'f','#include "public.h"\n')
    assert next(candidates)[0]=='assembly-byteview-redraft'
    with pytest.raises(ValueError,match='ABI mismatch'):next(candidates)
