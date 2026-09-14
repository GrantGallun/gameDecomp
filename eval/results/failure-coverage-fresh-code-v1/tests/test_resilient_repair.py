import hashlib
import json
from pathlib import Path

import pytest

from solver import modelrepair, repair_context, type_plan, type_transaction, workspace
from eval import completion_campaign


def test_type_plan_atomic_fields_and_abi_views(tmp_path):
    (tmp_path/'include').mkdir()
    (tmp_path/'include/api.h').write_text('typedef struct {int count;} Packet;\nvoid f(void *p);')
    source = '#include "api.h"\nvoid f(void *p) {\n    p->unk0 = 3;\n    p->unk0 += 2;\n}\n'
    abi = type_transaction.contract(tmp_path,source,'f')
    plan = {'hypothesis':'header view','types':[{'variable':'p','type':'Packet'}],
            'fields':[{'base':'p','field':'unk0','member':'count'}]}
    child,probe = type_plan.prepare(source,plan,'f',abi)
    assert 'void f(void *p)' in child
    assert 'Packet *typed_p = (Packet *)p;' in child
    assert child.count('typed_p->count') == 2
    assert '== 0' in probe and 'Packet' in probe
    with pytest.raises(ValueError,match='all connected'):
        type_plan.prepare(source,{**plan,'fields':[]},'f',abi)
    with pytest.raises(ValueError,match='dotted'):
        type_plan.prepare(source,{**plan,'fields':[{'base':'p','field':'unk0','member':'count; evil()'}]},'f',abi)
    # Every use is rebound against the present source, not stale line numbers.
    moved,_ = type_plan.prepare('// new line\n'+source,plan,'f',abi)
    assert moved == '// new line\n'+child


def test_plan_rejects_layout_probe_failure(monkeypatch,tmp_path):
    source='void f(void *p) { p->unk4=1; }'
    abi={'status':'unavailable','known_header_types':['Packet']}
    plan={'hypothesis':'wrong offset','types':[{'variable':'p','type':'Packet'}],
          'fields':[{'base':'p','field':'unk4','member':'count'}]}
    monkeypatch.setattr(type_plan.frontend_check,'check',lambda *a:{'passed':False,'diagnostics':'negative array size'})
    with pytest.raises(ValueError,match='layout probe'):
        type_plan.apply(tmp_path,tmp_path,source,plan,'f',abi,'build/src/x.o')


def test_context_projection_does_not_copy_body_or_unbalanced_scope(tmp_path):
    (tmp_path/'include').mkdir()
    (tmp_path/'src').mkdir()
    (tmp_path/'include/api.h').write_text('int f(void *p);')
    (tmp_path/'src/x.c').write_text('#include "api.h"\nint f(void *p) { SECRET_BODY(); }')
    source='void f(void *p) { return; }'
    child,report=repair_context.project(tmp_path,'f',source,'build/src/x.o')
    assert '#include "api.h"' in child and 'int f(void *p)' in child
    assert 'SECRET_BODY' not in child+json.dumps(report)
    assert not report['legacy_return_scope']
    unchanged,report=repair_context.project(tmp_path,'f',source,'build/src/../../evil.o')
    assert unchanged == source and report['status']=='unavailable'


def test_existing_c89_repair_runs_without_model_calls_and_preserves_lineage(monkeypatch,tmp_path):
    source='void f(void) {\n    work();\n    int x = compute();\n    use(x);\n}\n'
    root=workspace.Attempt(False,0,False,'','Syntax Error','',7,frontend={'passed':True})
    good=workspace.Attempt(True,70,False,'','','',8,frontend={'passed':True})
    seen=[]
    monkeypatch.setattr(workspace,'target_asm',lambda *a:'glabel f\njr ra\nnop')
    def score(*args,**kw):
        seen.append((args[3],kw))
        return good
    monkeypatch.setattr(workspace,'score',score)
    r=modelrepair.search(tmp_path,'f',source,tmp_path,model='test',endpoint='none',
        base_attempt=root,resilient=True,max_calls=0)
    assert r.best_attempt is good and r.normalization_candidates == 1
    assert seen[0][1]['parent_attempt_id']==7
    assert seen[0][0].index('work();') < seen[0][0].index('x = compute();')


def test_bitcast_recovery_uses_existing_adapter_without_model(monkeypatch,tmp_path):
    source='f32 f(u8 x) { return (bitwise f32) -(s32) x; }'
    root=workspace.Attempt(False,0,False,'','Syntax Error','',7,frontend={'passed':False})
    good=workspace.Attempt(True,70,False,'','','',8,frontend={'passed':True})
    seen=[]
    monkeypatch.setattr(workspace,'target_asm',lambda *a:'glabel f\njr ra\nnop')
    def score(*args,**kw):
        seen.append((args[3],kw))
        return good
    monkeypatch.setattr(workspace,'score',score)
    r=modelrepair.search(tmp_path,'f',source,tmp_path,model='test',endpoint='none',
        base_attempt=root,resilient=True,max_calls=0)
    assert r.best_attempt is good and r.normalization_candidates == 1 and r.calls_attempted == 0
    assert seen[0][1]['parent_attempt_id'] == 7
    assert seen[0][1]['strategy'] == 'modelrepair-normalize:bitcast-union'
    assert 'm2c_bits_0.from = -(s32) x' in seen[0][0]


def test_semantic_improvement_survives_lower_byte_score(monkeypatch,tmp_path):
    root=workspace.Attempt(True,95,False,'','','',1,frontend={'passed':True})
    child=workspace.Attempt(True,75,False,'','','',2,frontend={'passed':True})
    class Provider:
        provider_id='test'
        def generate(self,request):
            assert 'SEMANTIC REPAIR OBJECTIVE' in request.prompt
            return json.dumps({'kind':'expression','hypothesis':'fix return','edits':[{'old':'return 0;','new':'return 1;'}]}),{'done_reason':'stop'}
    monkeypatch.setattr(workspace,'score',lambda *a,**k:child)
    monkeypatch.setattr(workspace,'target_asm',lambda *a:'glabel f\nli v0,1\njr ra\nnop')
    monkeypatch.setattr(workspace,'assert_uncontaminated',lambda *a:None)
    def semantic(state):
        return {'panel_sha256':'fixed','semantic_key':[int('return 1;' in state.source)],'status':'observed','authoritative':False}
    r=modelrepair.search(tmp_path,'f','int f(void) { return 0; }',tmp_path,model='test',endpoint='none',
        base_attempt=root,provider=Provider(),resilient=True,semantic_evaluator=semantic,max_calls=1,draws=1)
    assert r.best_attempt is child and not r.exact
    assert r.best_byte.attempt is root and r.best_semantic.attempt is child
    assert {s.attempt.receipt_id for s in r.frontier} == {1,2}


def test_semantic_panels_cannot_be_mixed():
    a=workspace.Attempt(True,90,False,'','','')
    states=[modelrepair.CandidateState(str(i),a,semantic={'panel_sha256':str(i),'semantic_key':[i]}) for i in (1,2)]
    with pytest.raises(ValueError,match='different test panels'):
        modelrepair._frontier(states,2)


def test_campaign_routes_compile_success_to_semantic_worker():
    node={'status':'pending','source_sha256':'x','jobs':[],
          'residual':{'compiled':True,'frontend':{'passed':True}}}
    assert completion_campaign.next_profile(node,2)['name']=='semantic_handoff'
    node['semantic_validation']={'status':'observed_pass','authoritative':False}
    assert completion_campaign.next_profile(node,2)['name']=='local_rewrites'


@pytest.mark.parametrize('row',[None,4,[],{'variable':None,'type':'Packet'}])
def test_malformed_type_plan_is_a_rejection_not_controller_crash(row):
    plan={'hypothesis':'test','types':[row],'fields':[]}
    with pytest.raises(ValueError,match='invalid type view'):
        type_plan.prepare('void f(void *p) {}',plan,'f',{})


def test_header_packet_keeps_connected_types_before_command_union(tmp_path):
    from solver import compile_obligations
    (tmp_path/'include').mkdir()
    unrelated='\n'.join(f'typedef struct {{int x;}} Command{i};' for i in range(45))
    union='typedef union {'+' '.join(f'Command{i} a{i};' for i in range(45))+'} Commands;'
    (tmp_path/'include/api.h').write_text(unrelated+'\n'+union+'''
typedef struct {int count;} WaveLoop;
typedef struct {WaveLoop *loop;} WaveTable;
typedef struct {WaveTable *table;} LoadFilter;
''')
    rows=compile_obligations.header_types(tmp_path,'#include "api.h"\nCommands *f(void);','loadLoadParam')
    assert {'LoadFilter','WaveTable','WaveLoop'} <= {row['type'] for row in rows}


def test_campaign_retains_both_champions_before_incidental_alternatives():
    def row(n): return {'attempt_id':n,'source_sha256':str(n)}
    node={'source_sha256':'1','champions':{'semantic':row(1),'byte':row(7)},
          'frontier':[row(1),row(2),row(3),row(7)]}
    assert [r['attempt_id'] for r in completion_campaign.retained_candidates(node)]==[7,2,3]


def test_stalled_type_plan_switches_to_source_edits(monkeypatch,tmp_path):
    source='void f(void *p) { p->unk0=1; }'
    root=workspace.Attempt(False,0,False,'','bad member','',1,frontend={'passed':False})
    child=workspace.Attempt(True,70,False,'','','',2,frontend={'passed':True})
    prompts=[]
    class Provider:
        provider_id='test'
        def generate(self,request):
            prompts.append(request.prompt)
            if len(prompts)<=2:
                return json.dumps({'hypothesis':'bad','types':[],'fields':[]}),{'done_reason':'stop'}
            return json.dumps({'kind':'expression','hypothesis':'typed access',
                'edits':[{'old':'p->unk0=1;','new':'*(int *)p=1;'}]}),{'done_reason':'stop'}
    monkeypatch.setattr(workspace,'target_asm',lambda *a:'glabel f\njr ra\nnop')
    monkeypatch.setattr(workspace,'assert_uncontaminated',lambda *a:None)
    monkeypatch.setattr(workspace,'score',lambda *a,**k:child)
    r=modelrepair.search(tmp_path,'f',source,tmp_path,model='test',endpoint='none',
        base_attempt=root,provider=Provider(),resilient=True,retry_invalid=True,
        max_calls=3,max_depth=4,draws=1,exhaust_budget=True)
    assert r.best_attempt is child
    assert 'TYPE PLAN' in prompts[0] and 'TYPE PLAN' not in prompts[2]


def test_return_feedback_contains_real_register_producers():
    from types import SimpleNamespace
    from eval.semantic_lane import return_producers
    from solver.mips_differential import InstructionEvent
    event=InstructionEvent(0,0,'li v0,7',writes=(('v0',7,'constant 7'),))
    rows=return_producers(SimpleNamespace(trace=[event]),('v0',))
    assert rows[0]['writes'][0]['register']=='v0'
    assert rows[0]['writes'][0]['value']=='0x00000007'


def test_generated_child_c89_repair_uses_raw_child_as_parent(monkeypatch,tmp_path):
    root=workspace.Attempt(False,0,False,'','bad type','',5,frontend={'passed':False})
    bad=workspace.Attempt(False,0,False,'','Syntax Error','',6,frontend={'passed':True})
    good=workspace.Attempt(True,60,False,'','','',7,frontend={'passed':True})
    recorded=[]
    class Provider:
        provider_id='test'
        def generate(self,request):
            return json.dumps({'kind':'expression','hypothesis':'initialize',
                'edits':[{'old':'bad();','new':'work();\n    int x=compute();\n    use(x);'}]}),{'done_reason':'stop'}
    def score(*args,**kwargs):
        recorded.append(kwargs)
        return bad if len(recorded)==1 else good
    monkeypatch.setattr(workspace,'target_asm',lambda *a:'glabel f\njr ra\nnop')
    monkeypatch.setattr(workspace,'assert_uncontaminated',lambda *a:None)
    monkeypatch.setattr(workspace,'score',score)
    r=modelrepair.search(tmp_path,'f','void f(void) {\n    bad();\n}',tmp_path,
        model='test',endpoint='none',base_attempt=root,provider=Provider(),resilient=True,max_calls=1,draws=1)
    assert r.best_attempt is good and r.normalization_candidates==1
    assert [row['parent_attempt_id'] for row in recorded]==[5,6]


def test_semantic_panel_uses_real_runner_and_return_trace(monkeypatch,tmp_path):
    from eval import dag_pipeline_pilot as dag
    from eval.semantic_lane import Panel
    from solver import mips_differential as d
    (tmp_path/'target_object_dump_normalized.s').write_text('glabel f\nli v0,7\njr ra\nnop\n')
    (tmp_path/'child_object_dump_normalized.s').write_text('glabel f\nli v0,0\njr ra\nnop\n')
    monkeypatch.setattr(workspace,'semantic_assembly',lambda text,obj:text)
    monkeypatch.setattr(dag,'prototype_info',lambda *a:{'return_registers':['v0'],
        'mutable_scalar_registers':[],'pointer_registers':[]})
    monkeypatch.setattr(dag,'_seed_cases',lambda *a:(d.TestCase('test',1),))
    monkeypatch.setattr(dag,'_call_contracts',lambda *a:({},{}))
    panel=Panel(tmp_path,tmp_path,'f',max_cases=4,exploration_cases=4)
    state=modelrepair.CandidateState('int f(void) {return 0;}',
        workspace.Attempt(True,80,False,'','','',frontend={'passed':True}),tmp_path/'child.o')
    report=panel(state)
    assert report['counts']['failed']==report['total']
    assert report['feedback'][0]['return_producers']['target'][0]['text']=='li v0,7'
    assert report['source_sha256']==hashlib.sha256(state.source.encode()).hexdigest()
    assert report['authoritative'] is False


def test_semantic_panel_does_not_hide_unsupported_paths_behind_filtered_passes(monkeypatch,tmp_path):
    from eval import dag_pipeline_pilot as dag
    from eval.semantic_lane import Panel
    from solver import mips_differential as d, callee_execution as c
    # a1==0 returns safely; a1!=0 reaches a read of an uninitialized stack byte.
    assembly = '''addiu sp,sp,-64
sw ra,20(sp)
beqz a1,.Ldone
nop
addiu a0,sp,24
jal readWord
nop
.Ldone:
lw ra,20(sp)
addiu sp,sp,64
jr ra
nop'''
    (tmp_path/'target_object_dump_normalized.s').write_text(assembly)
    (tmp_path/'child_object_dump_normalized.s').write_text(assembly)
    monkeypatch.setattr(workspace,'semantic_assembly',lambda text,obj:text)
    monkeypatch.setattr(dag,'prototype_info',lambda *a:{'return_registers':[],
        'mutable_scalar_registers':['a1'],'pointer_registers':[]})
    monkeypatch.setattr(dag,'_seed_cases',lambda *a:(
        d.TestCase('safe',1,entry_registers=(('a1',0),)),d.TestCase('missing-effects',1,entry_registers=(('a1',1),))))
    monkeypatch.setattr(dag,'_call_contracts',lambda *a:({'readWord':1},{'readWord':{'arity_known':True}}))
    environment = c.Environment({'readWord':c.Leaf('lw v0,0(a0)\njr ra\nnop','synthetic')})
    panel=Panel(tmp_path,tmp_path,'f',max_cases=2,exploration_cases=2,callee_environment=environment)
    state=modelrepair.CandidateState('void f(void) {}',
        workspace.Attempt(True,80,False,'','','',frontend={'passed':True}),tmp_path/'child.o')
    report=panel(state)
    assert report['counts']=={'passed':report['total']}
    assert report['status']=='observed_pass_with_execution_debt'
    assert 'uninitialized' in report['execution_obstructions'][0]['error']


def test_semantic_panel_marks_opaque_stack_pass_as_debt_and_feeds_contracts(monkeypatch,tmp_path):
    from eval import dag_pipeline_pilot as dag
    from eval.semantic_lane import Panel
    from solver import mips_differential as d, callee_execution as c, compile_obligations
    assembly = ('addiu sp,sp,-64\nsw ra,20(sp)\nsw zero,24(sp)\n'
                'addiu a0,sp,24\njal opaque\nnop\nlw ra,20(sp)\n'
                'addiu sp,sp,64\njr ra\nnop')
    for name in ('target', 'child'):
        (tmp_path/(name+'_object_dump_normalized.s')).write_text(assembly)
    monkeypatch.setattr(workspace,'semantic_assembly',lambda text,obj:text)
    monkeypatch.setattr(dag,'prototype_info',lambda *a:{'return_registers':[],
        'mutable_scalar_registers':[],'pointer_registers':[]})
    monkeypatch.setattr(dag,'_seed_cases',lambda *a:(d.TestCase('test',1),))
    contracts = {'opaque':{'arity_known':False,'arity_source':'unresolved'}}
    monkeypatch.setattr(dag,'_call_contracts',lambda *a:({'opaque':1},contracts))
    panel=Panel(tmp_path,tmp_path,'f',max_cases=2,exploration_cases=2,callee_environment=c.Environment({}))
    state=modelrepair.CandidateState('void f(void) {}',
        workspace.Attempt(True,80,False,'','','',frontend={'passed':True}),tmp_path/'child.o')
    report=panel(state)
    assert report['counts']=={'passed':report['total']}
    assert report['status']=='observed_pass_with_execution_debt'
    assert report['call_contracts']==panel.report['call_contracts']==contracts
    assert all(o['case_count']==report['total'] for o in report['opaque_stack_obligations'])
    state.semantic=report
    monkeypatch.setattr(compile_obligations,'header_types',lambda *a,**kw:[])
    prompt=modelrepair.semantic_prompt(tmp_path,'f',state,assembly,{},[])
    assert 'opaque-stack-pointee-unmodeled' in prompt
    assert 'Unknown call arities are diagnostic assumptions' in prompt


def test_semantic_value_edit_rejects_block_header_mangling_and_retries(monkeypatch,tmp_path):
    source='void f(int x) {\n    if (x) {\n        store(0);\n    }\n}\n'
    root=workspace.Attempt(True,80,False,'','','',1,frontend={'passed':True})
    child=workspace.Attempt(True,70,False,'','','',2,frontend={'passed':True})
    prompts=[]; compiled=[]
    class Provider:
        provider_id='test'
        def generate(self,request):
            prompts.append(request.prompt)
            edit={'slot':'L2','new':'store(1);'} if len(prompts)==1 else {'old':'store(0);','new':'store(1);'}
            return json.dumps({'kind':'expression','hypothesis':'repair value','edits':[edit]}),{'done_reason':'stop'}
    def score(*args,**kw): compiled.append(args[3]); return child
    def semantic(state):
        return {'status':'observed_failure','semantic_key':[int('store(1);' in state.source)],
                'panel_sha256':'fixed','feedback':[]}
    monkeypatch.setattr(workspace,'target_asm',lambda *a:'glabel f\njr ra\nnop')
    monkeypatch.setattr(workspace,'assert_uncontaminated',lambda *a:None)
    monkeypatch.setattr(workspace,'score',score)
    r=modelrepair.search(tmp_path,'f',source,tmp_path,model='test',endpoint='none',
        base_attempt=root,provider=Provider(),resilient=True,semantic_evaluator=semantic,
        max_calls=2,draws=1,retry_invalid=True)
    assert len(compiled)==1 and r.best_attempt is child
    assert r.invalid_proposals==1 and 'ONE physical line' in prompts[1]


def test_type_plan_does_not_turn_unknown_scalar_arithmetic_into_pointer_math():
    source='void f(void *p) {\n    consume(p->unk0 + 1);\n}'
    plan={'hypothesis':'view','types':[{'variable':'p','type':'Packet'}],
          'fields':[{'base':'p','field':'unk0','member':'count'}]}
    with pytest.raises(ValueError,match='ambiguous member arithmetic'):
        type_plan.prepare(source,plan,'f',{'status':'unavailable','known_header_types':['Packet']})
