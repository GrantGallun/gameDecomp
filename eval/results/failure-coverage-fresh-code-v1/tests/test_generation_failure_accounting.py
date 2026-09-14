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
