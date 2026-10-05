"""Campaign entry point must generate, score and retain real frontend repairs."""
import pytest
from solver import frontend_diagnostics, frontend_fixits, modelrepair, workspace


CASES = [
    ('global-field-view', 'extern Record records[4];',
     'void f(s32 value) {\n    records.unk24 = value;\n}',
     "member reference base type 'Record[4]' is not a structure or union", '.',
     'lui t0,%hi(records)\naddiu t0,t0,%lo(records)\nsh a0,0x24(t0)\njr ra\nnop',
     '(*(s16 *)((unsigned char *)&records + 0x24))'),
    ('global-scalar-view', 'extern IndexValue indexValue;',
     'int f(void) {\n    return indexValue * 4;\n}',
     "invalid operands to binary expression ('IndexValue' and 'int')", '*',
     'lui t0,%hi(indexValue)\nlh v0,%lo(indexValue)(t0)\njr ra\nnop', '(*(s16 *)&indexValue)'),
    ('header-signature-view', 's32 f(u32);',
     's32 f(s32 arg0) {\n    return arg0;\n}', "conflicting types for 'f'", 'f',
     'jr ra\nmove v0,a0', 's32 arg0 = (s32)gd_abi_arg0;'),
    ('call-arity', 'int tick(int value);',
     'int f(int value, int spare) {\n    return tick(value, spare);\n}',
     'too many arguments to function call, expected single argument, have 2', 'spare',
     'jal tick\nnop\njr ra\nnop', '((void)(spare), tick(value))'),
    ('stack-scalar-arrays', '',
     'void f(void) {\n    s32 sp20;\n    sp20[0] = 1;\n    sp20[1] = 2;\n}',
     'subscripted value is not an array, pointer, or vector', '[',
     'addiu sp,sp,-64\nsw zero,0x20(sp)\nsw zero,0x24(sp)\naddiu sp,sp,64\njr ra\nnop',
     's32 sp20[2];'),
]


def setup(tmp_path,monkeypatch,header,source,diagnostics,assembly):
    (tmp_path/'include').mkdir(); (tmp_path/'include/api.h').write_text(header+'\n')
    obj=bytearray(52);obj[:6]=b'\x7fELF\x01\x02';obj[18:20]=(8).to_bytes(2,'big');obj[36:40]=(0x1000).to_bytes(4,'big')
    (tmp_path/'target.o').write_bytes(obj)
    root=workspace.Attempt(False,0,False,'','Syntax Error','',10,
        frontend={'passed':False,'diagnostics':'truncated old observation'}, compiler_recipe={'target':'build/f.o'})
    monkeypatch.setattr(workspace,'target_asm',lambda *a:assembly)
    monkeypatch.setattr(workspace,'assert_uncontaminated',lambda *a:None)
    def observe(code,**kw):
        assert code==source and kw['full_diagnostics']
        return dict(status='rejected',diagnostics=diagnostics)
    monkeypatch.setattr(frontend_diagnostics,'analyse',observe)
    monkeypatch.setattr(frontend_diagnostics,'recipe',lambda *a:dict(command=['clang']))
    monkeypatch.setattr(frontend_fixits,'run_frontend',lambda *a:(1,diagnostics))
    return root


@pytest.mark.parametrize('label,header,body,message,anchor,assembly,expected',CASES)
def test_campaign_normalization_scores_real_owner_and_keeps_verified_child(tmp_path,monkeypatch,
        label,header,body,message,anchor,assembly,expected):
    source='#include "api.h"\n'+body+'\n'
    number=2 if label=='header-signature-view' else 4 if label=='stack-scalar-arrays' else 3
    line=source.splitlines()[number-1]
    diag=f'candidate.c:{number}:{line.index(anchor)+1}: error: {message}\n {number} | {line}\n'
    root=setup(tmp_path,monkeypatch,header,source,diag,assembly)
    scored=[]
    def score(ws,repo,tag,code,**kwargs):
        scored.append((code,kwargs))
        return workspace.Attempt(True,100,True,'','','',11,frontend={'passed':True})
    monkeypatch.setattr(workspace,'score',score)
    result=modelrepair.search(tmp_path,'f',source,tmp_path,model='test',endpoint='none',
        base_attempt=root,resilient=True,max_calls=0)
    matching=[(s,k) for s,k in scored if k['action']==label]
    assert matching and expected in matching[0][0]
    assert matching[0][1]['parent_attempt_id']==10 and matching[0][1]['extra']
    assert expected in result.best_source and result.best_attempt.frontend['passed']
    assert result.normalization_candidates>=1


def test_campaign_casts_use_measured_partial_proposal_and_still_score(tmp_path,monkeypatch):
    source='int f(void *value) {\n    return value;\n}\n'
    diag="candidate.c:2:12:{2:12-2:17}: error: incompatible pointer to integer conversion returning 'void *' from a function with result type 'int'\n"
    root=setup(tmp_path,monkeypatch,'',source,diag,'jr ra\nmove v0,a0')
    monkeypatch.setattr(frontend_fixits,'run_frontend',lambda repo,code,*a:(1,diag) if code==source else (0,''))
    scored=[]
    def score(ws,repo,tag,code,**kw):
        scored.append(kw)
        return workspace.Attempt(True,100,True,'','','',11,frontend={'passed':True})
    monkeypatch.setattr(workspace,'score',score)
    result=modelrepair.search(tmp_path,'f',source,tmp_path,model='test',endpoint='none',
        base_attempt=root,resilient=True,max_calls=0)
    assert any(k['action']=='frontend-casts' for k in scored)
    assert '((int) (value))' in result.best_source


def test_rejected_proposal_cannot_promote_itself_to_compiled_or_exact(tmp_path,monkeypatch):
    source='#include "api.h"\nvoid f(s32 value) {\n    records.unk24 = value;\n}\n'
    diag="candidate.c:3:12: error: member reference base type 'Record[4]' is not a structure or union\n 3 |     records.unk24 = value;\n"
    root=setup(tmp_path,monkeypatch,'extern Record records[4];',source,diag,
        'lui t0,%hi(records)\naddiu t0,t0,%lo(records)\nsh a0,0x24(t0)\njr ra\nnop')
    # Equal compiler outcomes preserve the original incumbent. No generator can
    # set the verdict; its candidate is still passed to the scoring boundary.
    scored=[]
    def score(ws,repo,tag,code,**kw):
        scored.append(kw)
        return workspace.Attempt(False,0,False,'','Syntax Error','',11,
            frontend={'passed':False,'diagnostics':'truncated old observation'})
    monkeypatch.setattr(workspace,'score',score)
    result=modelrepair.search(tmp_path,'f',source,tmp_path,model='test',endpoint='none',
        base_attempt=root,resilient=True,max_calls=0)
    assert any(k['action']=='global-field-view' for k in scored)
    assert result.best_source==source and not result.exact and not result.best_attempt.compiled
