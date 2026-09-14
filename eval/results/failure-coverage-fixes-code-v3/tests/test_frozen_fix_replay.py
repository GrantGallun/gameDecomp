import importlib
import json

import pytest


replay=importlib.import_module('eval.experiments.campaign-gap-audit.replay_fresh_fixes_v1')


def checkpoints(tmp_path):
    paths=[]
    for batch in range(2):
        nodes={}
        for i in range(8):
            name=f'f{batch}_{i}'
            source=tmp_path/(name+'.c')
            source.write_text('void '+name+'(void) { /* \u2013 */ }\n',encoding='utf-8')
            nodes[name]={'status':'pending','source':str(source),
                         'source_sha256':replay.sha(source),'attempt_id':100+i}
        nodes[f'f{batch}_7']={'status':'parked','blocker':{'slice_sha256':'bound elsewhere'}}
        path=tmp_path/f'batch{batch}.json'
        path.write_text(json.dumps({'status':'paused_budget','nodes':nodes}),encoding='utf-8')
        paths.append(path)
    return paths


def test_final_source_selection_is_bound_and_preserves_parked(tmp_path):
    paths=checkpoints(tmp_path)
    rows=replay.selections(paths)
    assert len(rows)==16
    assert sum(bool(row['source']) for row in rows)==14
    assert rows[0]['historical_attempt_id']==100
    assert rows[0]['checkpoint_sha256']==replay.sha(paths[0])
    with pytest.raises(ValueError,match='overlap'):
        replay.selections([paths[0],paths[0]])
    with pytest.raises(ValueError,match='two preselected'):
        replay.selections(paths[:1])


@pytest.mark.parametrize('change,match',[
    ('inflight','terminal'),('status','terminal'),('missing','missing source'),('hash','hash mismatch')])
def test_final_source_selection_fails_closed(tmp_path,change,match):
    paths=checkpoints(tmp_path)
    state=json.loads(paths[0].read_text())
    if change=='inflight':
        state['inflight']={'function':'f0_0'}
    elif change=='status':
        state['status']='running'
    elif change=='missing':
        state['nodes']['f0_0']['source']=None
    else:
        state['nodes']['f0_0']['source_sha256']='bad'
    paths[0].write_text(json.dumps(state),encoding='utf-8')
    with pytest.raises(ValueError,match=match):
        replay.selections(paths)


def test_no_model_provider_refuses_even_accidental_generation():
    with pytest.raises(AssertionError,match='model access forbidden'):
        replay.NoModel().generate(None)


def test_replay_audit_checks_source_and_exact_certificate(tmp_path):
    audit=importlib.import_module('eval.experiments.campaign-gap-audit.audit_fixes_replay_v1')
    source=tmp_path/'best.c'
    source.write_text('void f(void) {}\n',encoding='utf-8',newline='\n')
    digest=replay.sha(source)
    original={'function':'f','source_sha256':'historical'}
    result={'calls_attempted':0,'generations':0,'best_source_path':str(source),
        'best_source_sha256':digest,'exact':True,
        'best_residual':{'compiled':True,'frontend':{'passed':True},'exact':True},
        'verification':{'exact':True,'candidate_source_sha256':digest}}
    receipt={'config':{'function':'f'},'root':{'source_sha256':'historical'},'result':result}
    assert audit.bound_result(receipt,original)[1]==source.read_text()
    assert audit.stage(result)=='accepted_object_exact'
    result['semantic_validation']={'source_sha256':'stale'}
    with pytest.raises(ValueError,match='semantic source'):
        audit.bound_result(receipt,original)
    result.pop('semantic_validation')
    result['verification']['candidate_source_sha256']='wrong'
    with pytest.raises(ValueError,match='certificate source'):
        audit.bound_result(receipt,original)
    result['verification']={}
    with pytest.raises(ValueError,match='agreeing certificate'):
        audit.bound_result(receipt,original)


def test_replay_audit_keeps_debt_and_unavailable_separate():
    audit=importlib.import_module('eval.experiments.campaign-gap-audit.audit_fixes_replay_v1')
    result={'exact':False,'best_residual':{'compiled':True,'frontend':{'passed':True}},
        'semantic_validation':{'status':'observed_pass_with_execution_debt','counts':{'passed':64}}}
    assert audit.stage(result)=='sampled_pass_with_debt_nonexact'
    result['semantic_validation']={'status':'unavailable'}
    assert audit.stage(result)=='semantic_untested_or_unavailable'
    result['semantic_validation']={'status':'observed_failure','counts':{'failed':64}}
    assert audit.stage(result)=='differential_disagreement'
