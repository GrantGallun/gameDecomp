import hashlib
import importlib
import json
from pathlib import Path


def test_campaign_audit_uses_worker_utf8_not_locale_default(tmp_path, monkeypatch):
    module=importlib.import_module('eval.experiments.campaign-gap-audit.summarize')
    source='void f(void) { /* non-ASCII: \u2013 \ufffd */ }\n'
    candidate=tmp_path/'candidate.c'
    candidate.write_text(source,encoding='utf-8')
    checkpoint=tmp_path/'state.json'
    checkpoint.write_text(json.dumps({'status':'paused_budget','regime':'development',
        'nodes':{'f':{'status':'pending','source':str(candidate),
            'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'residual':{'compiled':True,'frontend':{'passed':True}},'jobs':[]}}}),encoding='utf-8')
    original=Path.read_text
    def locale_read(path, *args, **kwargs):
        if path==candidate and not args and 'encoding' not in kwargs:
            return candidate.read_bytes().decode('cp1252')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',locale_read)
    report=module.summarize(checkpoint)
    assert report['rows'][0]['source_sha256']==hashlib.sha256(source.encode()).hexdigest()
