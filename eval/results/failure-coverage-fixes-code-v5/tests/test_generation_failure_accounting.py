import importlib


def test_failed_generation_is_not_an_invalid_source_hypothesis():
    module=importlib.import_module('eval.experiments.campaign-gap-audit.summarize')
    job={'path':'bound-job.json','sha256':'bound-hash','invalid_proposals':0,
         'generation_failures':['depth 1 draw 1: generation failed (TimeoutError)']}
    result=module.accounting({},'not_compiling','',[job])
    finding=next(row for row in result['findings'] if row['symptom']=='model-generation-failed')
    assert finding['evidence']['sha256']=='bound-hash'
    assert 'TimeoutError' in finding['evidence']['events'][0]
    assert 'absent generation' in finding['next_action']
    assert result['causal_accounting_complete'] is False


def test_successful_generation_does_not_acquire_a_transport_failure():
    module=importlib.import_module('eval.experiments.campaign-gap-audit.summarize')
    result=module.accounting({},'not_compiling','',[{'generation_failures':[]}])
    assert not any(row['symptom']=='model-generation-failed' for row in result['findings'])


def test_unavailable_target_execution_keeps_bound_diagnostic_route():
    module=importlib.import_module('eval.experiments.campaign-gap-audit.summarize')
    semantic={'status':'unavailable','source_sha256':'source','panel_sha256':'panel',
        'target_execution':{'status_counts':{'memory_fault':5}},
        'target_coverage':{'completed_runs':0}}
    result=module.accounting({'semantic_validation':semantic},'semantic_untested_or_unavailable','')
    finding=next(row for row in result['findings'] if row['symptom']=='target-execution-unavailable')
    assert finding['evidence']['source_sha256']=='source'
    assert finding['evidence']['execution']==semantic['target_execution']
    assert result['causal_accounting_complete'] is False


def test_indirect_abi_debt_routes_even_with_sampled_pass():
    module=importlib.import_module('eval.experiments.campaign-gap-audit.summarize')
    obligation={'kind':'indirect-call-arity-unmodeled','call_ordinal':1,
                'next_action':'Bind independently supported callback ABI.'}
    result=module.accounting({'semantic_validation':{'indirect_call_obligations':[obligation]}},
                            'sampled_pass_with_debt_nonexact','')
    row=next(r for r in result['findings'] if r['symptom']=='indirect-call-arity-unmodeled')
    assert row['evidence'] == obligation and row['owner']=='solver/callee_execution.py'
    assert result['causal_accounting_complete'] is False
