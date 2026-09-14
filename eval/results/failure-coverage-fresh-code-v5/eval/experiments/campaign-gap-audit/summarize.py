"""Read campaign receipts and emit disjoint stopping stages, not match guesses."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from eval import agentrepair


def local_path(value):
    if Path.cwd().drive and value.startswith('/mnt/c/'):
        return Path('C:/' + value[len('/mnt/c/'):])
    return Path(value)


def accounting(node, stage, source, jobs=()):
    """Observed symptoms route investigation; they never certify a root cause."""
    residual = node.get('residual') or {}
    frontend = residual.get('frontend') or {}
    semantic = node.get('semantic_validation') or {}
    diagnostic = frontend.get('diagnostics','') + '\n' + residual.get('compiler_error_signature','')
    findings = []
    environment = semantic.get('environment_obligations') or {}
    if environment.get('kind') == 'hardware-register-environment-required':
        findings.append({'symptom':environment['kind'], 'owner':'solver/hardware_environment.py',
            'accounting_status':'explicit_environment_gap', 'evidence':environment,
            'next_action':environment['next_action']})
    if semantic.get('status') == 'unavailable' and semantic.get('target_execution'):
        findings.append({'symptom':'target-execution-unavailable','owner':'eval/semantic_lane.py',
            'accounting_status':'needs_causal_diagnosis',
            'evidence':{'source_sha256':semantic.get('source_sha256'),
                'panel_sha256':semantic.get('panel_sha256'),
                'execution':semantic['target_execution'],
                'coverage':semantic.get('target_coverage')},
            'next_action':'Inspect target failure inputs and callee effects; noncompletion does not prove candidate failure or target nontermination.'})
    for job in jobs:
        failures=job.get('generation_failures',[])
        if failures:
            findings.append({'symptom':'model-generation-failed','owner':'solver/llm.py',
                'accounting_status':'mechanically_detected_unrepaired',
                'evidence':{'receipt':job['path'],'sha256':job['sha256'],'events':failures},
                'next_action':'Inspect transport/timeout and retry policy; absent generation is not a rejected repair hypothesis.'})
    rules = (
        ('absent-compiled-function-body', r'Compiled object has no text symbols', 'eval/completion_campaign.py'),
        ('incomplete-type', r'incomplete (?:definition|type)', 'solver/type_constraints.py'),
        ('pointer-view-conflict', r'member reference base type|incompatible pointer types', 'solver/type_plan.py'),
        ('callback-abi-conflict', r'incompatible function pointer', 'solver/type_transaction.py'),
        ('integer-pointer-conflict', r'incompatible (?:integer to pointer|pointer to integer)', 'solver/type_transaction.py'),
        ('undeclared-identifier', r'undeclared identifier|undefined;', 'solver/compile_recovery.py'),
        ('missing-function-declaration', r'implicit declaration of function', 'solver/compile_recovery.py'),
        ('syntax-unresolved', r'expected |Syntax Error', 'solver/m2c_context.py'),
        ('declaration-type-conflict', r'conflicting types for|redefinition of|must use .* tag', 'solver/type_transaction.py'),
        ('missing-record-member', r'no member named', 'solver/type_plan.py'),
        ('assignment-type-conflict', r'assigning to .* from incompatible type', 'solver/type_transaction.py'),
    )
    for label, pattern, owner in rules:
        lines = [line for line in diagnostic.splitlines() if re.search(pattern,line)]
        if lines:
            findings.append({'symptom':label,'owner':owner,'evidence':lines[:4],
                'accounting_status':'needs_causal_diagnosis',
                'next_action':'Inspect the source-bound failing expression and the existing generator decline; replay a minimal causal change.'})
    if source and re.search(r'\(bitwise\s+',source):
        findings.append({'symptom':'unlowered-bitcast','owner':'solver/m2c_context.py',
            'accounting_status':'needs_causal_diagnosis',
            'evidence':[line for line in source.splitlines() if '(bitwise ' in line][:4],
            'next_action':'Verify operand evaluation and bit width; test the existing lowering dialect on this expression.'})
    for contract in semantic.get('callee_source_contracts',[]):
        if contract.get('status') == 'result-width-conflict':
            findings.append({'symptom':'binary-candidate-result-width-conflict','owner':'solver/callee_execution.py',
                'accounting_status':'mechanically_detected_unrepaired','evidence':contract,
                'next_action':'Repair result declaration and all affected consumers atomically, then rerun the fixed panel.'})
    for obligation in semantic.get('opaque_stack_obligations',[]):
        findings.append({'symptom':'opaque-stack-pointee-unmodeled','owner':'solver/callee_execution.py',
            'accounting_status':'mechanically_detected_unrepaired','evidence':obligation,
            'next_action':obligation['next_action']})
    for obligation in semantic.get('indirect_call_obligations',[]):
        findings.append({'symptom':'indirect-call-arity-unmodeled','owner':'solver/callee_execution.py',
            'accounting_status':'mechanically_detected_unrepaired','evidence':obligation,
            'next_action':obligation['next_action']})
    for name, contract in semantic.get('call_contracts',{}).items():
        if contract.get('arity_known') is False:
            findings.append({'symptom':'unknown-call-arity','owner':'eval/dag_pipeline_pilot.py',
                'accounting_status':'mechanically_detected_unrepaired',
                'evidence':{'callee':name,'contract':contract},
                'next_action':'Recover a binary/header-backed ABI; do not promote the candidate declaration to authority.'})
    if stage == 'parked':
        findings.append({'symptom':(node.get('blocker') or {}).get('status','unknown-blocker'),
            'owner':'solver/target_intake.py','accounting_status':'boundary_requires_audit',
            'evidence':node.get('blocker'),
            'next_action':'Verify the positive unsupported-instruction or operational evidence and safe routing; do not treat parking alone as accounted.'})
    if stage == 'differential_disagreement':
        findings.append({'symptom':'observable-disagreement','owner':'eval/semantic_lane.py',
            'accounting_status':'needs_causal_diagnosis','evidence':semantic.get('feedback',[])[:1],
            'next_action':'Replay the exact source and panel; separate emulator/callee-contract gaps from candidate errors.'})
    if stage == 'semantic_inconclusive':
        findings.append({'symptom':'incomplete-comparison','owner':'eval/semantic_lane.py',
            'accounting_status':'needs_execution_diagnosis','evidence':semantic.get('outcome_accounting',semantic.get('counts')),
            'next_action':'Inspect target/candidate execution status and opaque object/callee obligations; do not classify missing comparison as a source disagreement.'})
    if stage in {'sampled_pass_nonexact','sampled_pass_with_debt_nonexact'}:
        findings.append({'symptom':'exactness-residual','owner':'solver/residual.py',
            'accounting_status':'needs_causal_diagnosis','evidence':residual.get('faults',{}),
            'next_action':'Revalidate semantics/coverage under current inputs, classify the byte residual and test an applicable rewrite.'})
    if not findings and stage != 'accepted_object_exact':
        findings.append({'symptom':'unclassified','owner':'eval/completion_campaign.py',
            'accounting_status':'unclassified','evidence':diagnostic,
            'next_action':'Inspect worker receipts; missing evidence is not a solved or understood failure.'})
    return {'findings':findings, 'causal_accounting_complete':False,
        'semantic_debt':semantic.get('debt',[]),
        'execution_obstructions':semantic.get('execution_obstructions',[]),
        'note':'Routing/symptom detection only; source and receipt bindings do not prove a fix or causal diagnosis.'}


def summarize(path):
    raw = path.read_bytes()
    state = json.loads(raw)
    if state.get('inflight'):
        raise ValueError('campaign still has an unfinished work item: ' + str(path))
    rows, actual_calls = [], 0
    for name, node in sorted(state['nodes'].items()):
        residual = node.get('residual') or {}
        frontend = residual.get('frontend') or {}
        semantic = node.get('semantic_validation') or {}
        if semantic and semantic.get('source_sha256') != node.get('source_sha256'):
            raise ValueError('semantic source identity mismatch: ' + name)
        if node['status'] == 'parked': stage = 'parked'
        elif residual.get('compiled') is not True: stage = 'not_compiling'
        elif frontend.get('passed') is not True: stage = 'frontend_rejected_or_unavailable'
        elif node['status'] in {'object_exact','integrated'}: stage = 'accepted_object_exact'
        elif semantic.get('status') == 'observed_pass': stage = 'sampled_pass_nonexact'
        elif semantic.get('status') == 'observed_pass_with_execution_debt': stage = 'sampled_pass_with_debt_nonexact'
        elif (semantic.get('counts') or {}).get('failed', 0): stage = 'differential_disagreement'
        elif (semantic.get('counts') or {}).get('inconclusive',0) or semantic.get('status')=='inconclusive': stage = 'semantic_inconclusive'
        else: stage = 'semantic_untested_or_unavailable'
        source = None
        if node.get('source'):
            # Worker sources and their hashes use UTF-8. Windows' locale default
            # can decode valid UTF-8 as another string and invent an integrity
            # mismatch despite identical artifact bytes and database source.
            source = local_path(node['source']).read_text(encoding='utf-8')
            if hashlib.sha256(source.encode()).hexdigest() != node.get('source_sha256'):
                raise ValueError('candidate source identity mismatch: '+name)
        job_evidence = []
        for job in node['jobs']:
            receipt_bytes = local_path(job['receipt']).read_bytes()
            receipt = json.loads(receipt_bytes)
            result = receipt.get('result') or receipt
            calls = result.get('calls_attempted',0) or 0
            actual_calls += calls
            job_evidence.append({'path':job['receipt'],'sha256':hashlib.sha256(receipt_bytes).hexdigest(),
                'profile':job['profile'],'calls_attempted':calls,
                'transport_events':result.get('transport_events'),
                'invalid_proposals':result.get('invalid_proposals'),
                'incomplete_responses':result.get('incomplete_responses'),
                'compiling_children':result.get('compiling_children'),
                'generation_failures':[event for event in result.get('log',[])
                    if isinstance(event,str) and 'generation failed (' in event]})
        rows.append({'function':name, 'stage':stage, 'attempt_id':node.get('attempt_id'),
            'source_sha256':node.get('source_sha256'), 'score':node.get('score'),
            'positional_byte_distance':residual.get('positional_byte_distance'),
            'target_text_bytes':residual.get('target_text_bytes'),
            'semantic_counts':semantic.get('counts'), 'target_coverage':semantic.get('target_coverage'),
            'semantic_debt':semantic.get('debt'), 'blocker':node.get('blocker'),
            'first_frontend_errors':[line for line in frontend.get('diagnostics','').splitlines()
                                     if 'error:' in line][:4],
            'profiles':[job['profile'] for job in node['jobs']],
            'job_evidence':job_evidence,'accounting':accounting(node,stage,source,job_evidence)})
    return {'checkpoint':str(path), 'checkpoint_sha256':hashlib.sha256(raw).hexdigest(),
        'status':state['status'], 'cohort_size':len(rows), 'actual_model_calls':actual_calls,
        'stages':dict(Counter(row['stage'] for row in rows)), 'rows':rows,
        'regime':state['regime'], 'forked_from':state.get('forked_from'),
        'complete_c_decompilation':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkpoints',type=Path,nargs='+')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists(): raise ValueError('refusing to overwrite audit')
    receipt={'kind':'campaign-stopping-stage-audit','campaigns':[summarize(p) for p in args.checkpoints]}
    agentrepair._atomic_json(args.output,receipt)
    for row in receipt['campaigns']:
        print(json.dumps({k:row[k] for k in ('checkpoint','status','cohort_size','actual_model_calls','stages')}))


if __name__ == '__main__': main()
