"""Capability expectations must not be manufactured from observed success."""
from copy import deepcopy
import importlib
from pathlib import Path

import pytest

from eval.search_replay import digest
from test_search_scheduler import compiler, context

ROOT = Path(__file__).resolve().parents[1]
ASM = 'glabel f\n  addu $v0, $a0, $a1\n  jr $ra\n  nop\n'
SOURCE = 'int f(int a, int b) { return a+b; }'


def modules():
    return (importlib.import_module('solver.capability_map'),
            importlib.import_module('solver.capability_contracts'))


def assess(*, source=SOURCE, assembly=ASM, score=-1, facts=None, connected='theory'):
    m,c = modules()
    call,_ = compiler({source:score})
    return m.assess(source,'f',call(source,'root',None),assembly=assembly,
        context=context(source),contracts=c.catalog(ROOT),facts=facts or {},connected=connected)


def row(report, name):
    return next(r for r in report['capabilities'] if r['id']==name)


def test_declared_draft_capability_does_not_depend_on_observed_candidate_success():
    r=assess(facts={'m2c_available':True,'big_endian_o32':True})
    assert row(r,'m2c_draft')['applicability']=='expected-within-contract'
    assert r['goals']['exact_c']['status']=='undetermined'
    assert r['goals']['semantic_c']['status']=='undetermined'
    assert r['composition']['complete_constructor_available'] is False


def test_known_observation_is_a_witness_without_claiming_recovered_intent():
    r=assess(score=100)
    assert r['goals']['exact_c']['status']=='demonstrably-reachable'
    assert r['goals']['exact_c']['receipt_id']==1
    assert r['goals']['original_source']['status']=='not-identifiable-from-this-evidence'


def test_missing_tool_is_a_prerequisite_gap_not_an_impossible_binary():
    r=assess(facts={'m2c_available':False,'big_endian_o32':True})
    assert row(r,'m2c_draft')['applicability']=='missing-prerequisite'
    assert r['global_impossibility_established'] is False


@pytest.mark.parametrize('assembly',[ '', ASM.replace('addu','mystery_op')])
def test_empty_or_unclassified_instruction_input_never_gets_full_coverage(assembly):
    r=assess(assembly=assembly,facts={'m2c_available':True,'big_endian_o32':True})
    assert row(r,'m2c_draft')['applicability']!='expected-within-contract'
    assert r['requirements']['coverage_complete'] is False


def test_unresolved_indirect_jump_remains_an_explicit_composition_debt():
    r=assess(assembly='glabel f\n jr $t0\n nop\n')
    assert r['requirements']['indirect_control']
    assert 'indirect-control-targets' in r['composition']['unresolved']


def test_wide_mechanism_elsewhere_is_not_mistaken_for_absent_capability():
    src='void f(void) { sink(/* u64+0x0 */ p->unk10, /* u64+0x4 */ p->unk14); }'
    r=assess(source=src)
    w=row(r,'wide_operations')
    assert w['needed'] and not w['connected']
    assert 'compile-recovery' in w['callers']
    assert w['status']=='available-not-connected'
    assert 'measured_layouts' in w['unknown_prerequisites']


def test_byte_view_shape_is_outside_current_wide_reconstruction_domain():
    src='void f(void) { sink(/* u64+0x0 */ (*(u32 *)(p+16)), /* u64+0x4 */ (*(u32 *)(p+20))); }'
    r=assess(source=src,connected='compile-recovery')
    assert row(r,'wide_operations')['applicability']=='outside-declared-domain'
    assert 'named_wide_fields' in row(r,'wide_operations')['failed_domain']


def test_unknown_prerequisites_do_not_become_true_by_optimism():
    r=assess()
    assert row(r,'m2c_draft')['applicability']=='undetermined'
    assert r['goals']['semantic_c']['status']=='undetermined'


def test_registry_binds_real_owners_tests_and_callers():
    _,c=modules()
    cat=c.catalog(ROOT)
    assert len(cat['contracts'])>=12
    for contract in cat['contracts']:
        for ref in contract['references']:
            assert (ROOT/ref['path']).is_file()
            assert len(ref['sha256'])==64
    assert next(x for x in cat['contracts'] if x['id']=='semantic_check')['output']=='finite-behavioral-evidence'


def test_rehashed_derived_claim_is_rejected():
    m,_=modules()
    r=assess()
    r['goals']['semantic_c']['status']='demonstrably-reachable'
    r['sha256']=digest({k:v for k,v in r.items() if k!='sha256'})
    with pytest.raises(ValueError,match='differs'):
        m.validate_assessment(r)


def test_stale_verdict_is_rejected():
    m,c=modules()
    call,_=compiler({SOURCE:20})
    v=call(SOURCE,'root',None)
    with pytest.raises(ValueError,match='binding'):
        m.assess(SOURCE+' ', 'f',v,assembly=ASM,context=context(SOURCE),contracts=c.catalog(ROOT),facts={},connected='theory')


def test_failed_expectation_retains_contract_and_requests_diagnosis():
    m,_=modules()
    parent=assess(facts={'m2c_available':True,'big_endian_o32':True})
    child=assess(source=SOURCE+' ',facts={'m2c_available':True,'big_endian_o32':True})
    result=m.compare(parent,child,contract_id='m2c_draft')
    assert result['status']=='construction-observed-validation-open'
    assert result['action']=='investigate-composition-or-representation'
    assert result['proven_implementation_bug'] is False


def test_bounded_checker_is_not_a_semantic_constructor():
    r=assess(score=50,facts={'bounded_semantic_pass':True})
    assert r['goals']['semantic_c']['status']=='undetermined'
    assert 'all-input-equivalence' in r['composition']['unresolved']


def test_workspace_assessment_reads_no_reference_body(tmp_path):
    m,_=modules()
    ws=tmp_path/'nonmatchings/f';ws.mkdir(parents=True)
    (ws/'target.s').write_text(ASM)
    (tmp_path/'include').mkdir()
    (tmp_path/'include/api.h').write_text('extern int f(int a, int b);\n')
    source='#include "api.h"\n'+SOURCE
    call,_=compiler({source:-1})
    r=m.workspace_assessment(source,'f',call(source,'root',None),context=context(source),repo=tmp_path,workspace=ws)
    assert r['inputs']['facts']['header_abi_locked'] is True
    assert r['inputs']['facts']['target_available'] is False
    assert not r['requirements']['reference_bodies_used']


def test_planner_retains_capabilities_and_failed_construction_diagnosis():
    from eval.theory_planner import TheoryOnline
    from eval.repair_planner import run_planner, replay_planner
    from test_repair_planner import empty_model
    from test_repair_theory import route
    m,c=modules();cat=c.catalog(ROOT)
    callback,receipts=compiler({'start':-1,'child':-1})
    ctx=context()
    def assess_candidate(source,verdict):
        return m.assess(source,'f',verdict,assembly=ASM,context=ctx,contracts=cat,
            facts={'m2c_available':True,'big_endian_o32':True},connected='theory')
    def routes(source,verdict):
        return [route('byteview_redraft','child')] if source=='start' else []
    def factory(*args,**kwargs):
        return TheoryOnline(*args,**kwargs,inspect_routes=routes,capability_assessor=assess_candidate)
    env=factory('start',callback,lambda *_:iter(()),ctx)
    r=run_planner(env,empty_model(),budget=3)
    assert len(receipts)==2
    caps=r['theory']['capability_envelope']
    assert set(caps['assessments'])=={'root','root/0'}
    assert caps['transitions'][0]['status']=='construction-observed-validation-open'
    assert r==replay_planner(env.world,empty_model(),lambda *_:iter(()),budget=3,environment_factory=factory)


def test_planner_rejects_assessment_for_other_source():
    from eval.theory_planner import TheoryOnline
    from eval.repair_planner import run_planner
    from test_repair_planner import empty_model
    callback,_=compiler({'start':-1})
    wrong=assess()
    env=TheoryOnline('start',callback,lambda *_:iter(()),context(),inspect_routes=lambda *_:[],
        capability_assessor=lambda *_:wrong)
    with pytest.raises(ValueError,match='capability.*binding'):
        run_planner(env,empty_model(),budget=1)


def test_false_exact_flag_does_not_witness_a_capability():
    m,c=modules()
    callback,_=compiler({SOURCE:20});v=callback(SOURCE,'root',None)
    v['exact']=True
    with pytest.raises(ValueError,match='witness'):
        m.assess(SOURCE,'f',v,assembly=ASM,context=context(SOURCE),contracts=c.catalog(ROOT),facts={},connected='theory')


def test_opaque_assembly_bytes_cannot_disappear_from_requirement_coverage():
    r=assess(assembly='glabel f\n .word 0xffffffff\n jr $ra\n nop\n',
             facts={'m2c_available':True,'big_endian_o32':True})
    assert not r['requirements']['coverage_complete']
    assert r['requirements']['unknown_instructions']
    assert row(r,'m2c_draft')['applicability']!='expected-within-contract'


def test_catalog_does_not_confuse_shared_module_import_with_specific_wiring():
    _,c=modules()
    rows={r['id']:r for r in c.catalog(ROOT)['contracts']}
    assert 'model-repair' not in rows['scalar_members']['callers']
    assert 'model-repair' not in rows['byteview_redraft']['callers']
    assert 'model-repair' not in rows['m2c_draft']['callers']
    assert 'model-repair' not in rows['storage_search']['callers']
    assert 'compile-recovery' not in rows['frontend_abi']['callers']


def test_compatible_widths_do_not_imply_signature_repair_domain(tmp_path):
    m,_=modules();ws=tmp_path/'nonmatchings/f';ws.mkdir(parents=True)
    (tmp_path/'include').mkdir();(tmp_path/'include/api.h').write_text('extern int f(int a, int b);')
    source='#include "api.h"\n'+SOURCE
    callback,_=compiler({source:-1})
    r=m.workspace_assessment(source,'f',callback(source,'root',None),context=context(source),repo=tmp_path,workspace=ws)
    assert row(r,'header_signature')['applicability']=='outside-declared-domain'
    assert 'signature_conflict_site' in row(r,'header_signature')['failed_domain']


def test_expected_constructor_declining_emission_creates_an_investigation():
    from eval.theory_planner import TheoryOnline
    from eval.repair_planner import run_planner
    from test_repair_planner import empty_model
    m,c=modules();cat=c.catalog(ROOT);ctx=context()
    callback,receipts=compiler({'start':-1})
    def assessor(source,v):
        return m.assess(source,'f',v,assembly=ASM,context=ctx,contracts=cat,
            facts={'m2c_available':True,'big_endian_o32':True},connected='theory')
    routes=[dict(id='m2c_draft',owner='solver.m2c_input',addresses=['members'],requires=[],
                 status='blocked',reason='no emitted candidate',candidates=[])]
    env=TheoryOnline('start',callback,lambda *_:iter(()),ctx,inspect_routes=lambda *_:routes,
                     capability_assessor=assessor)
    result=run_planner(env,empty_model(),budget=2)
    conflicts=result['theory']['capability_envelope']['expectation_conflicts']
    assert len(receipts)==1 and len(conflicts)==1
    assert conflicts[0]['status']=='expected-construction-not-observed'
    assert conflicts[0]['proven_implementation_bug'] is False


def test_actual_array_member_domain_is_expected_without_running_generator(tmp_path):
    m,_=modules()
    source='void f(void) {\n short a[64]; a.unk2 = 1;\n}'
    column=source.splitlines()[1].index('.unk2')+1
    callback,_=compiler({source:-1});v=callback(source,'root',None)
    v['frontend'].update(status='rejected',diagnostics=f"candidate.c:2:{column}: error: member reference base type 'short[64]' is not a structure or union\n1 error generated.\n")
    r=m.workspace_assessment(source,'f',v,context=context(source),repo=tmp_path,workspace=tmp_path)
    assert row(r,'scalar_members')['applicability']=='expected-within-contract'


def test_real_signature_domain_is_expected_independently_of_emission(tmp_path):
    from test_header_signature_view import SOURCE as source
    from test_theory_repairs import target
    m,_=modules();ws=target(tmp_path)
    (tmp_path/'include').mkdir();(tmp_path/'include/api.h').write_text('s32 f(Actual *state, u32 value);')
    callback,_=compiler({source:-1});v=callback(source,'root',None)
    v.pop('verification')
    v['frontend'].update(status='rejected',diagnostics="candidate.c:2:5: error: conflicting types for 'f'\n 2 | "+source.splitlines()[1]+"\n1 error generated.\n")
    import hashlib
    ctx={**context(source),'target_sha256':hashlib.sha256((ws/'target.o').read_bytes()).hexdigest()}
    r=m.workspace_assessment(source,'f',v,context=ctx,repo=tmp_path,workspace=ws)
    assert row(r,'header_signature')['applicability']=='expected-within-contract'


def test_retained_route_decline_can_be_audited_without_rerunning_generator():
    m,_=modules();a=assess(facts={'m2c_available':True,'big_endian_o32':True})
    rows=[dict(id='m2c_draft',owner='solver.m2c_input',status='blocked',reason='declined',candidates=[])]
    conflicts=m.inspect_expectations(a,rows)
    assert len(conflicts)==1 and conflicts[0]['receipt_id']==1
    assert conflicts[0]['owner_report']['reason']=='declined'


def test_candidate_outside_intended_domain_is_a_guard_investigation():
    m,_=modules()
    a=assess(facts={'indexable_member_base':False})
    b=assess(source=SOURCE+' ',facts={'indexable_member_base':False})
    result=m.compare(a,b,contract_id='scalar_members')
    assert result['status']=='outside-contract-construction'
    assert result['action']=='investigate-generator-domain-guard'
