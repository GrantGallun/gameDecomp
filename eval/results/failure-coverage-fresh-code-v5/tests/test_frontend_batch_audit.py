import hashlib
import importlib
import json
import pytest

audit=importlib.import_module('eval.experiments.campaign-gap-audit.audit_frontend_batch').audit


def fixture(tmp_path):
    source='void f(void) {}\n';digest=hashlib.sha256(source.encode()).hexdigest()
    (tmp_path/'child.best.c').write_text(source)
    (tmp_path/'child.json').write_text(json.dumps({'result':{'best_source_sha256':digest,
        'normalization_candidates':0,'best_residual':{'compiled':False,'frontend':{'passed':False,'diagnostics':'error'}}}}))
    path=tmp_path/'batch.json'
    path.write_text(json.dumps({'status':'complete','rows':[{'function':'new_unknown',
        'receipt':'/external/child.json','source_sha256':digest}]}))
    return path


def test_unknown_routes_remain_explicit(tmp_path):
    result=audit(fixture(tmp_path))
    assert result['unclassified']==['new_unknown']
    assert result['rows'][0]['repair_status']=='unresolved'


def test_changed_source_and_unfinished_batch_rejected(tmp_path):
    path=fixture(tmp_path)
    (tmp_path/'child.best.c').write_text('changed')
    with pytest.raises(ValueError,match='source identity'):audit(path)
    path.write_text('{"status":"running"}')
    with pytest.raises(ValueError,match='terminal'):audit(path)
