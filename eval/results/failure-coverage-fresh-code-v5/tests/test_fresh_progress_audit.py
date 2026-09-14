import hashlib
import importlib
import json
import pytest

audit = importlib.import_module('eval.experiments.campaign-gap-audit.audit_fresh_progress_v2')


@pytest.mark.parametrize('tamper', [None,'output','semantic','root','calls','checkpoint'])
def test_probe_bindings_reject_mismatched_evidence(tmp_path,tamper):
    source = tmp_path/'candidate.c'
    source.write_text('int f(void) {return 1;}')
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    checkpoint = tmp_path/'checkpoint.json'
    checkpoint.write_text(json.dumps({'nodes':{'f':{'source_sha256':'original'}}}))
    checkpoint_hash = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    inputs = {'function':'f','integration_requested':False,'reference_bodies_used':False,
        'model_calls':0,'source_sha256':'original','checkpoint_sha256':checkpoint_hash}
    receipt = {'root':{'source_sha256':'original'},'result':{
        'calls_attempted':0,'best_attempt_id':1,'best_source_path':str(source),
        'best_source_sha256':source_hash,'best_residual':{'compiled':True,'frontend':{'passed':True}},
        'semantic_validation':{'source_sha256':source_hash,'status':'observed_pass'}}}
    if tamper == 'output': source.write_text('changed')
    if tamper == 'semantic': receipt['result']['semantic_validation']['source_sha256']='other'
    if tamper == 'root': receipt['root']['source_sha256']='other'
    if tamper == 'calls': receipt['result']['calls_attempted']=1
    if tamper == 'checkpoint': inputs['checkpoint_sha256']='other'
    path = tmp_path/'probe.json'
    path.write_text(json.dumps(receipt))
    (tmp_path/'probe-inputs.json').write_text(json.dumps(inputs))
    if tamper:
        with pytest.raises(ValueError): audit.bind_probe(checkpoint,'f',path)
    else:
        result = audit.bind_probe(checkpoint,'f',path)
        assert result['source_sha256']==source_hash
        assert not result['causal_accounting_complete']
