import json

import pytest

from solver import type_constraints as tc, type_plan


def field(name,offset,width=4,pointee=None):
    return {'member':name,'offset':offset,'width':width,'pointee':pointee,'array':False,'owner_size':64}


def fixture():
    source='''void f(void *owner, int command, void *param) {
    void *table;
    void *loop;
    owner->unk28 = param;
    table = owner->unk28;
    consume(param->unk8);
    loop = table->unkC;
    consume(loop->unk0);
}
'''
    asm='glabel f\nsw a2,0x28(a0)\nlbu v0,8(a2)\njr ra\nnop\n'
    layouts={
        'Owner':[field('table',40,pointee='Table')],
        'Table':[field('type',8,1),field('wave.a.loop',12,pointee='LoopA'),field('wave.b.loop',12,pointee='LoopB')],
        'WrongParam':[field('type',8,2)],
        'LoopA':[field('start',0)],'LoopB':[field('start',0)]}
    return source,asm,layouts


def test_flow_and_binary_width_narrow_types_but_keep_union_ambiguity():
    source,asm,layouts=fixture()
    report=tc.solve(source,'f',asm,layouts)
    assert report['status']=='candidates'
    assert report['domains']['owner']==['Owner']
    assert report['domains']['param']==report['domains']['table']==['Table']
    assert report['domains']['loop']==['LoopA','LoopB']
    assert len(report['plans'])==2 and not report['truncated']
    for plan in report['plans']:
        candidate,_=type_plan.prepare(source,plan,'f',{'status':'unavailable','known_header_types':list(layouts)})
        assert 'Table *typed_param' in candidate and 'typed_param->type' in candidate


def test_incompatible_header_dialect_is_not_an_impossibility_claim():
    source,asm,layouts=fixture()
    del layouts['Table']
    report=tc.solve(source,'f',asm,layouts)
    assert report['status']=='inconsistent' and not report['plans']
    assert 'supplied header/direct-access dialect' in report['reason']


def test_plan_budget_reports_unexplored_alternatives():
    source,asm,layouts=fixture()
    report=tc.solve(source,'f',asm,layouts,max_plans=1)
    assert len(report['plans'])==1 and report['truncated']


def test_null_assignment_does_not_merge_unrelated_pointer_objects():
    source,asm,layouts=fixture()
    source=source.replace('    owner->unk28', '    table = NULL;\n    loop = NULL;\n    owner->unk28',1)
    report=tc.solve(source,'f',asm,layouts)
    assert report['status']=='candidates' and len(report['plans'])==2
    assert 'NULL' not in report['domains']


def test_binary_load_width_breaks_ordinary_field_tie():
    source='void f(void *p) { use(p->unk8); }'
    layouts={'Byte':[field('kind',8,1)],'Half':[field('kind',8,2)]}
    r=tc.solve(source,'f','glabel f\nlbu v0,8(a0)\njr ra\nnop',layouts)
    assert r['domains']['p']==['Byte']
    assert any(e['type']=='Half' for e in r['eliminations'])


def test_ast_flattening_uses_record_identity_and_preserves_pointer_types():
    ast={'inner':[
        {'kind':'RecordDecl','id':'child','tagUsed':'struct','name':'Child','completeDefinition':True,
         'inner':[{'kind':'FieldDecl','name':'value','type':{'qualType':'int'}}]},
        {'kind':'RecordDecl','id':'owner','tagUsed':'struct','name':'Owner','completeDefinition':True,
         'inner':[{'kind':'FieldDecl','name':'embedded','type':{'qualType':'Child'}},
                  {'kind':'FieldDecl','name':'next','type':{'qualType':'Child *','desugaredQualType':'struct Child *'}},
                  {'kind':'FieldDecl','name':'bits','isBitfield':True,'type':{'qualType':'int'}}]},
        {'kind':'TypedefDecl','name':'Child','inner':[{'kind':'RecordType','decl':{'id':'child'}}]},
        {'kind':'TypedefDecl','name':'Owner','inner':[{'kind':'RecordType','decl':{'id':'owner'}}]}]}
    result=tc.declarations(ast,['Owner','Child'])
    assert [r['member'] for r in result['Owner']]==['embedded.value','next']
    assert result['Owner'][1]['pointee']=='Child'
    probe,rows=tc.probe_source('#include "api.h"',result)
    assert 'sizeof(((Owner *)0)->embedded.value)' in probe
    assert len(rows)==3 and 'decomp_layout_values' in probe


def test_non_void_draft_is_explicitly_declined():
    r=tc.solve('void f(T *p) { use(p->unk8); }','f','glabel f\njr ra\nnop',{})
    assert r['status']=='declined'
