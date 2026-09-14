import hashlib
import importlib
import json
import pytest

inventory=importlib.import_module('eval.experiments.campaign-gap-audit.frontend_inventory')


def setup(tmp_path):
    results=tmp_path/'eval/results'
    results.mkdir(parents=True)
    for v in (3,4):
        for b in (1,2):
            nodes={'f':{'status':'pending','source_sha256':'original','residual':{'compiled':False}}} if (v,b)==(4,1) else {}
            (results/f'failure-coverage-fresh-paired-v{v}-batch-{b}.json').write_text(json.dumps({'inflight':None,'nodes':nodes}))
    (results/'frontend-v3-replay-v2.json').write_text(json.dumps({'rows':[],'checkpoints':[]}))
    cp=results/'failure-coverage-fresh-paired-v4-batch-1.json'
    binding={'function':'f','checkpoint':str(cp),'checkpoint_sha256':inventory.digest(cp),'source_sha256':'original'}
    (results/'v4-test-replay-v1-inputs.json').write_text(json.dumps(binding))
    source=results/'v4-test-replay-v1.best.c'
    source.write_text('void f(void) {}')
    result={'best_attempt_id':1,'best_source_sha256':inventory.digest(source),
        'best_residual':{'compiled':True,'frontend':{'passed':True}},
        'semantic_validation':{'status':'observed_failure'}}
    (results/'v4-test-replay-v1.json').write_text(json.dumps({'result':result}))
    return results


def test_compilation_inventory_does_not_promote_semantics(tmp_path):
    setup(tmp_path)
    report=inventory.build(tmp_path)
    assert report['original_blockers']==1 and report['remaining']==0
    assert report['rows'][0]['compilation_unblocked_evidence'][0]['semantic_status']=='observed_failure'


def test_rejects_changed_candidate(tmp_path):
    results=setup(tmp_path)
    (results/'v4-test-replay-v1.best.c').write_text('void f(void) { changed(); }')
    with pytest.raises(ValueError,match='source mismatch'): inventory.build(tmp_path)


def test_rejects_changed_checkpoint(tmp_path):
    results=setup(tmp_path)
    path=results/'failure-coverage-fresh-paired-v4-batch-1.json'
    path.write_text(path.read_text()+'\n')
    with pytest.raises(ValueError,match='checkpoint mismatch'): inventory.build(tmp_path)


def test_additional_campaign_and_its_bound_replay_are_included(tmp_path):
    results=setup(tmp_path)
    for b in (1,2):
        nodes={'g':{'status':'stalled','source_sha256':'fresh','residual':{'compiled':False}}} if b==1 else {}
        (results/f'failure-coverage-fresh-paired-v5-batch-{b}.json').write_text(json.dumps({'inflight':None,'nodes':nodes}))
    report=inventory.build(tmp_path,(3,4,5))
    assert (report['original_blockers'],report['remaining'])==(2,1)
    cp=results/'failure-coverage-fresh-paired-v5-batch-1.json'
    (results/'v5-test-replay-v1-inputs.json').write_text(json.dumps({'function':'g',
        'checkpoint':str(cp),'checkpoint_sha256':inventory.digest(cp),'source_sha256':'fresh'}))
    source=results/'v5-test-replay-v1.best.c';source.write_text('void g(void) {}')
    (results/'v5-test-replay-v1.json').write_text(json.dumps({'result':{
        'best_attempt_id':2,'best_source_sha256':inventory.digest(source),
        'best_residual':{'compiled':True,'frontend':{'passed':True}}}}))
    report=inventory.build(tmp_path,(3,4,5))
    assert (report['original_blockers'],report['remaining'])==(2,0)
