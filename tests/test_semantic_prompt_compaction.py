import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from solver import modelrepair, prompt_budget


def expand_table(value):
    if value.get('format') != 'named-column-tables-v1':
        return value
    return {name: dict(zip(table['columns'], row))
            for table in value['tables'] for name, row in table['rows'].items()}


def packet():
    contracts = {f'callee_{i}': {'prototype':f'int callee_{i}(int value)', 'arity_known':True,
        'argument_words':1, 'return_registers':['v0'], 'issues':['finite test'],
        'unknown_future_constraint':{'required':True}, 'active_selection':{
            'prototype':f'int callee_{i}(int value)', 'authority':'configured header',
            'includes':'#include "provided.h"\n'*12,
            'frontend_recipe':{'command':['clang']+['-DDEFINED=1']*50,
                'settings':{'long_recipe':'x'*1500}, 'target':'build/a.o',
                'warning_policy':'explicit errors preserved', 'checker_sha256':'checker'},
            'header_probe_sha256': 'probe'}} for i in range(5)}
    # Null and missing fields must never collapse into one table shape.
    contracts['unknown'] = {'arity_known':False,'argument_words':None,'issues':['unresolved']}
    primary = {'input':{'seed':7,'entry_registers':[['a0',0]],'global_writes':[['g',4,99]]},
        'reasons':['wrong call value'], 'causal_feedback':'exact first causal value tree',
        'path_context':{'contexts':[{'target':{'decisions':[{'instruction':3,'decision':'taken',
            'operands':[{'register':'a0','value':'0x0','provenance':'load g'}]}]},
            'candidate':{'decisions':[{'instruction':7,'decision':'not taken'}]}}]},
        'source_sha256':'source','panel_sha256':'panel',
        'passing_path_contrast':{'input':{'seed':8},'path_context':{'unusual_guard':'preserve exactly'}},
        'concrete_callee_executions':{'target':{'calls':[{'callee':'leaf','identity':'binary',
            'execution':{'status':'returned','return_values':{'v0':3},'error':'','abi_violations':[]},
            'instruction_prefix':[{'text':'trace','provenance':'long detail'*30}]*6,
            'instruction_suffix':[], 'omitted_trace_events':9}],'omitted_calls':4}},
        'unknown_future_obligation':{'binding':'must survive'}}
    return {'source_sha256':'source','panel_sha256':'panel','debt':['opaque calls'],
        'call_contracts':contracts,'primary_counterexample':primary,
        'other_failure_classes':[{'input':{'seed':9},'path_context':{'branch':7}}],
        'callee_environment':{'leaves':{},'assumed_outputs':{'output':'assumption'},'callback_programs':{}},
        'unknown_future_obligation':{'opaque':False},'source_object_obligations':['extent remains']}


def test_projection_preserves_primary_inputs_paths_contrasts_and_obligations():
    original = packet()
    before = copy.deepcopy(original)
    result = prompt_budget.semantic_packet(original)
    assert original == before
    for key in ('source_sha256','panel_sha256','debt','source_object_obligations','unknown_future_obligation',
                'other_failure_classes','callee_environment'):
        assert result[key] == original[key]
    for key,value in original['primary_counterexample'].items():
        if key != 'concrete_callee_executions':
            assert result['primary_counterexample'][key] == value
    call = result['primary_counterexample']['concrete_callee_executions']['target']['calls'][0]
    prior = original['primary_counterexample']['concrete_callee_executions']['target']['calls'][0]
    assert call['execution'] == prior['execution'] and call['identity'] == prior['identity']
    ref = result['_prompt_projection']['referenced_observations'][
        'primary_counterexample.concrete_callee_executions.target.calls.0.instruction_prefix']
    encoded=json.dumps(prior['instruction_prefix'],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    assert ref == {'sha256':hashlib.sha256(encoded).hexdigest(),'json_bytes':len(encoded),'items':6}
    assert call['instruction_suffix'] == [] and call['omitted_trace_events'] == 9
    assert prompt_budget.semantic_packet(original) == result
    altered=copy.deepcopy(original)
    altered['primary_counterexample']['input']['seed']=10
    assert prompt_budget.semantic_packet(altered)['_prompt_projection']['full_packet_sha256'] != result['_prompt_projection']['full_packet_sha256']


def test_tables_retain_all_contract_fields_and_unknown_null_distinctions():
    original=packet()
    result=prompt_budget.semantic_packet(original)
    expanded=expand_table(result['call_contracts'])
    assert result['call_contracts']['format'] == 'named-column-tables-v1'
    for name, prior in original['call_contracts'].items():
        expected=copy.deepcopy(prior)
        if expected.get('active_selection'):
            del expected['active_selection']['includes']
            del expected['active_selection']['frontend_recipe']['command']
            del expected['active_selection']['frontend_recipe']['settings']
        assert expanded[name] == expected
    assert expanded['unknown']['argument_words'] is None
    assert 'active_selection' not in expanded['unknown']


def test_secondary_fallback_omits_whole_case_with_exact_reference_only():
    original=packet()
    result=prompt_budget.semantic_packet(original,omit_secondary=True)
    assert result['primary_counterexample'] == prompt_budget.semantic_packet(original)['primary_counterexample']
    assert 'other_failure_classes' not in result
    ref=result['_prompt_projection']['referenced_observations']['other_failure_classes']
    assert ref['items']==1 and len(ref['sha256'])==64
    assert original['other_failure_classes'][0]['input']=={'seed':9}


def test_semantic_prompt_preserves_complete_c_and_assembly_and_uses_bounded_fallback(monkeypatch,tmp_path):
    from solver import compile_obligations
    monkeypatch.setattr(compile_obligations,'header_types',lambda *a,**kw:[])
    raw=packet()
    report={**raw,'feedback':[raw['primary_counterexample'],*raw['other_failure_classes']]}
    source='void f(void) { int value = 1; }\n'+'/* source line */\n'*1600
    asm='glabel f\njr ra\nnop\n'+'# target instruction annotation\n'*550
    state=SimpleNamespace(source=source,semantic=report)
    prompt=modelrepair.semantic_prompt(tmp_path,'f',state,asm,{},[])
    assert '\nCURRENT C:\n'+source+'\nACTUAL INCLUDED' in prompt
    assert '\nTARGET INSTRUCTIONS (read-only; byte polish is secondary while behavior fails):\n'+asm in prompt
    projected=json.loads(prompt.split('FAILING OBSERVABLES AND EXECUTED VALUE TREES:\n',1)[1].split('\nCURRENT C:\n',1)[0])
    assert projected['primary_counterexample']['path_context']==raw['primary_counterexample']['path_context']
    assert projected['_prompt_projection']['referenced_observations']['other_failure_classes']['items']==1
    # Oversized irreducible input is still refused; projection is not a bypass.
    with pytest.raises(prompt_budget.ContextBudgetError):
        prompt_budget.context_budget(prompt,6000,num_ctx=32768,response_schema=modelrepair.EDIT_SCHEMA)
