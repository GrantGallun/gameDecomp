import importlib
from types import SimpleNamespace as S
from eval.semantic_lane import outcome_accounting


def row(status,case='case',candidate='returned',error=''):
    return S(status=status,case=case,reasons=['boundary'] if status=='inconclusive' else [],
        target=S(status='returned',error=''),candidate=S(status=candidate,error=error))


def test_no_disagreement_is_not_reported_as_failure_or_pass():
    report=outcome_accounting([row('passed'),row('inconclusive')],execution_debt=True)
    assert report['status']=='inconclusive'
    assert report['observed_disagreements']==0
    assert report['inconclusive_comparisons']==1
    assert report['inconclusive_reason_groups'][0]['count']==1
    assert outcome_accounting([row('failed'),row('inconclusive')])['status']=='observed_failure'
    assert outcome_accounting([row('passed')])['status']=='observed_pass'
    assert outcome_accounting([row('passed')],execution_debt=True)['status']=='observed_pass_with_execution_debt'
    assert outcome_accounting([])['status']=='inconclusive'


def test_inconclusive_reasons_account_for_omitted_cases():
    rows=[row('inconclusive',case=str(i),candidate='memory_fault',error=str(i)) for i in range(12)]
    r=outcome_accounting(rows)
    assert len(r['inconclusive_reason_groups'])==8
    assert r['omitted_reason_groups']==4
    assert r['omitted_inconclusive_cases']==4
    assert sum(g['count'] for g in r['inconclusive_reason_groups'])+r['omitted_inconclusive_cases']==12


def test_auditor_separates_legacy_inconclusive_counts_from_disagreement():
    audit=importlib.import_module('eval.experiments.campaign-gap-audit.audit_fixes_replay_v1')
    r={'best_residual':{'compiled':True,'frontend':{'passed':True}},'exact':False,
        'semantic_validation':{'status':'observed_failure','counts':{'passed':20,'inconclusive':44}}}
    assert audit.stage(r)=='semantic_inconclusive'
    r['semantic_validation']['counts']['failed']=1
    assert audit.stage(r)=='differential_disagreement'
