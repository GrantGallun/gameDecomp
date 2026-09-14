import json

import pytest

from eval import completion_campaign
from solver import modelrepair, type_transaction as transaction, workspace


def test_atomic_many_slot_edits_and_legacy_limit():
    source = 'void f(void) {\n' + ''.join(f'    int v{i};\n' for i in range(6)) + '}\n'
    raw = json.dumps({'kind':'declarations','hypothesis':'connected storage views',
        'edits':[{'slot':f'L{i+2}','new':f'    short v{i};'} for i in range(6)]})
    with pytest.raises(ValueError, match='1 to 4'):
        modelrepair.parse_proposal(raw, source=source)
    p = modelrepair.parse_proposal(raw, source=source, type_transaction=True)
    child = modelrepair.apply_proposal(source, p, type_transaction=True)
    assert child.count('short') == 6
    with pytest.raises(ValueError, match='identity'):
        modelrepair.apply_proposal(source+'\n', p, type_transaction=True)
    bad = json.dumps({'kind':'declarations','hypothesis':'ambiguous',
        'edits':[{'old':'int','new':'short'}]})
    with pytest.raises(ValueError, match='slot/new'):
        modelrepair.parse_proposal(bad, source=source, type_transaction=True)


def test_transaction_cannot_change_directives_or_signature(tmp_path):
    (tmp_path/'include').mkdir()
    (tmp_path/'include'/'api.h').write_text('int f(void *filter, int id, void *param);')
    source = '#include "api.h"\nvoid f(void *a, int b, void *c) {\nreturn;\n}'
    abi = transaction.contract(tmp_path, source, 'f')
    assert abi['status'] == 'locked'
    fixed = source.replace('void f(', 'int f(')
    transaction.validate(source, fixed, 'f', abi)
    with pytest.raises(ValueError, match='ABI lock'):
        transaction.validate(source, fixed.replace('void *a','Thing *a'), 'f', abi)
    # A correct prototype cannot conceal an incompatible implementation.
    with pytest.raises(ValueError, match='ABI lock'):
        transaction.validate(source, 'int f(void*, int, void*);\n'+source, 'f', abi)
    with pytest.raises(ValueError, match='preprocessor'):
        transaction.validate(source, fixed+'\n#define a ((Thing*)a)\n', 'f', abi)


def test_complex_or_conflicting_header_abi_is_explicitly_unlocked(tmp_path):
    (tmp_path/'include').mkdir()
    (tmp_path/'include'/'api.h').write_text('int f(void*);\nvoid f(void*);')
    assert transaction.contract(tmp_path, '#include "api.h"', 'f')['status'] == 'ambiguous'
    assert transaction.signature('int f(void (*cb)(int));', 'f') is None


def test_existing_diagnostic_scope_is_not_part_of_public_return_type():
    source = ('CLANG_DIAGNOSTIC_PUSH\nCLANG_DIAGNOSTIC_IGNORE_RETURN_TYPE\n'
              'int f(void *p) {\nreturn;\n}\nCLANG_DIAGNOSTIC_POP\n')
    abi = {'status':'locked', 'shape':transaction.signature('int f(void *p);','f')}
    transaction.validate(source, source, 'f', abi)
    with pytest.raises(ValueError, match='ABI lock'):
        transaction.validate(source, source.replace('int f(', 'void f('), 'f', abi)
    with pytest.raises(ValueError, match='ABI lock'):
        transaction.validate(source, source.replace('void *p', 'int *p'), 'f', abi)
    # Unknown declaration tokens do not get discarded by the decoration rule.
    assert transaction.signature('SOMETHING\nint f(void *p);','f') != abi['shape']


def test_header_typedef_collision_is_rejected_before_compilation(tmp_path):
    (tmp_path/'include').mkdir()
    (tmp_path/'include'/'api.h').write_text('typedef struct {int x;} Packet;\nvoid f(void);')
    source='#include "api.h"\nvoid f(void) {}'
    abi=transaction.contract(tmp_path,source,'f')
    candidate=source.replace('void f', 'typedef struct {short y;} Packet;\nvoid f')
    with pytest.raises(ValueError,match='already define.*Packet'):
        transaction.validate(source,candidate,'f',abi)
    transaction.validate(source,candidate.replace('} Packet;', '} LocalPacket;'),'f',abi)


def test_type_edits_preserve_control_lines_but_can_retype_conditions():
    source='void f(void *p) {\nif (p->x) { work(); }\n}'
    abi={'status':'unavailable'}
    transaction.validate(source,source.replace('p->x','((Packet *)p)->field'),'f',abi)
    with pytest.raises(ValueError,match='control structure'):
        transaction.validate(source,source.replace('if (p->x) { work(); }','Packet *q=p;'),'f',abi)


def test_packet_indexes_dependent_uses_and_returns_without_inventing_types():
    source = 'void f(void *p) {\nvoid *q;\nq = p->table;\nq->loop->count = 1;\nreturn;\n}'
    packet = transaction.packet(source, '', 'f', {'status':'unavailable'})
    assert packet['pointer_assignment_candidates'] == [{'slot':'L3','destination':'q','source':'p->table'}]
    assert packet['member_uses'][-1] == {'slot':'L4','base':'q','members':['loop','count']}
    assert packet['return_sites'] == [{'slot':'L5','statement':'return;'}]
    assert packet['public_abi']['status'] == 'unavailable'


def test_return_feed_accounts_for_delay_slot_and_reports_unknown():
    p=transaction.packet('int f(void) { return 0; }',
        'glabel f\nli v0,7\njr ra\nli v0,9','f',{'status':'unavailable'})
    assert p['target_return_registers'][0]['v0_after_delay_slot'] == '0x9'
    p=transaction.packet('int f(void) { return 0; }',
        'glabel f\njr ra\nnop','f',{'status':'unavailable'})
    assert p['target_return_registers'][0]['v0_after_delay_slot'] == 'unresolved'


def failed(errors, ident):
    return workspace.Attempt(False,0,False,'','compile error','',ident, frontend={
        'passed':False,'diagnostics':'\n'.join(f'candidate.c:{i}:1: error: field' for i in range(errors))})


def test_temporary_error_increase_gets_bounded_followup_and_true_parent(monkeypatch, tmp_path):
    source = 'void f(void) {\n    void *p;\n}\n'
    root, intermediate = failed(1,1), failed(3,2)
    good = workspace.Attempt(True,75,False,'','','',3,frontend={'passed':True})
    prompts, parents = [], []
    class Provider:
        provider_id = 'test'
        def generate(self, request):
            prompts.append(request.prompt)
            assert request.response_schema['properties']['edits']['maxItems'] == 64
            assert 'old' not in request.response_schema['properties']['edits']['items']['properties']
            return (json.dumps({'kind':'declarations','hypothesis':'type then dependent use',
                'edits':[{'slot':'L2','new':'    Thing *p;' if len(prompts)==1 else '    int *p;'}]}),
                {'done_reason':'stop'})
    monkeypatch.setattr(workspace,'target_asm',lambda *a:'glabel f\njr ra\nnop')
    monkeypatch.setattr(workspace,'assert_uncontaminated',lambda *a:None)
    def score(*args,**kw):
        parents.append(kw['parent_attempt_id'])
        return intermediate if len(parents)==1 else good
    monkeypatch.setattr(workspace,'score',score)
    result = modelrepair.search(tmp_path,'f',source,tmp_path,model='test',endpoint='unused',
        provider=Provider(),base_attempt=root,type_transaction=True,compile_only=True,
        draws=1,max_calls=2,max_depth=2,beam_width=2)
    assert 'Thing *p;' in prompts[1]
    assert parents == [1,2]
    assert result.best_attempt is good and not result.exact and result.calls_attempted == 2
    assert any('lookahead' in row for row in result.log)


def test_transaction_invalid_output_does_not_end_remaining_budget(monkeypatch, tmp_path):
    class Provider:
        provider_id = 'test'
        def generate(self, request):
            return 'not json', {'done_reason':'stop'}
    monkeypatch.setattr(workspace,'target_asm',lambda *a:'glabel f\njr ra\nnop')
    monkeypatch.setattr(workspace,'assert_uncontaminated',lambda *a:None)
    result=modelrepair.search(tmp_path,'f','void f(void) {}',tmp_path,model='test',endpoint='unused',
        provider=Provider(),base_attempt=failed(1,1),type_transaction=True,
        draws=1,max_calls=3,max_depth=4)
    assert result.calls_attempted == 3 and result.invalid_proposals == 3
    assert not result.best_attempt.compiled


def test_lookahead_is_bounded_and_does_not_replace_best(monkeypatch, tmp_path):
    source='void f(void) {\n    int *p;\n}\n'
    root=failed(1,1)
    parents=[]
    class Provider:
        provider_id='test'
        def generate(self, request):
            return json.dumps({'kind':'declarations','hypothesis':'candidate view',
                'edits':[{'slot':'L2','new':f'    Type{len(parents)} *p;'}]}), {'done_reason':'stop'}
    monkeypatch.setattr(workspace,'target_asm',lambda *a:'glabel f\njr ra\nnop')
    monkeypatch.setattr(workspace,'assert_uncontaminated',lambda *a:None)
    def score(*a,**kw):
        parents.append(kw['parent_attempt_id'])
        return failed(len(parents)+2,len(parents)+1)
    monkeypatch.setattr(workspace,'score',score)
    result=modelrepair.search(tmp_path,'f',source,tmp_path,model='test',endpoint='unused',
        provider=Provider(),base_attempt=root,type_transaction=True,
        draws=1,max_calls=4,max_depth=4,beam_width=2)
    assert parents == [1,2,3,1]
    assert result.best_attempt is root and result.best_source == source
    assert any(s.attempt.receipt_id == 5 for s in result.frontier)


def test_campaign_prioritizes_connected_void_member_failures():
    n={'status':'pending','source_sha256':'a','jobs':[],'residual':{'compiled':False,
       'frontend':{'diagnostics':"member reference base type 'void'\nmember reference base type 'void'"}}}
    assert completion_campaign.next_profile(n,6)['name'] == 'compile_recovery'
    n['jobs'].append({'source_sha256':'a','profile':'compile_recovery'})
    assert completion_campaign.next_profile(n,6)['name'] == 'typed_transaction'
    n['residual']['compiled']=True
    assert completion_campaign.next_profile(n,6)['name'] == 'local_rewrites'


def test_type_packet_does_not_misreport_pointer_stores_as_pointer_copies():
    from solver import type_transaction
    source='''void f(void) {
    p = owner->next;
    *p = value;
    object.p = value;
    owner->p = value;
    if (ready) p = value;
    p = other; q = p;
}'''
    packet=type_transaction.packet(source,'glabel f\njr ra\nnop','f',{'status':'unavailable'})
    assert [(r['destination'],r['source']) for r in packet['pointer_assignment_candidates']]==[
        ('p','owner->next'),('p','other'),('q','p')]
    assert len(packet['declined_assignment_forms'])==4
    assert packet['omitted_assignment_forms']==0
