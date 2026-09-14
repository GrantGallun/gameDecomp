import importlib
import hashlib
import json
import pytest

audit = importlib.import_module('eval.experiments.campaign-gap-audit.summarize')


def test_symptom_labels_do_not_claim_causal_closure():
    node = {'residual':{'frontend':{'diagnostics':"error: incomplete definition of type X"}}}
    report = audit.accounting(node,'not_compiling','void f(void) {}')
    assert report['findings'][0]['symptom'] == 'incomplete-type'
    assert report['findings'][0]['owner'] == 'solver/type_constraints.py'
    assert not report['causal_accounting_complete']
    assert audit.accounting({},'semantic_untested_or_unavailable',None)['findings'][0]['symptom'] == 'unclassified'


def test_unsupported_is_evidence_to_audit_not_automatic_closure():
    node = {'blocker':{'status':'hardware_backend_required','instructions':[{'opcode':'mtc0'}]}}
    row = audit.accounting(node,'parked',None)['findings'][0]
    assert row['accounting_status'] == 'boundary_requires_audit'
    assert row['evidence']['instructions'][0]['opcode'] == 'mtc0'


def test_audit_checks_source_and_hashes_job_receipts(tmp_path):
    source = tmp_path/'f.c'
    source.write_text('void f(void) {}')
    receipt = tmp_path/'job.json'
    receipt.write_text(json.dumps({'calls_attempted':2,'invalid_proposals':1}))
    checkpoint = tmp_path/'checkpoint.json'
    checkpoint.write_text(json.dumps({'status':'paused_budget','regime':'test','nodes':{'f':{
        'status':'pending','source':str(source),'source_sha256':hashlib.sha256(source.read_text().encode()).hexdigest(),
        'jobs':[{'profile':'schema_patch','receipt':str(receipt)}]}}}))
    result = audit.summarize(checkpoint)
    assert result['actual_model_calls'] == 2
    assert result['rows'][0]['job_evidence'][0]['invalid_proposals'] == 1
    assert result['rows'][0]['job_evidence'][0]['sha256'] == hashlib.sha256(receipt.read_bytes()).hexdigest()
    source.write_text('changed')
    with pytest.raises(ValueError,match='candidate source identity'):
        audit.summarize(checkpoint)
