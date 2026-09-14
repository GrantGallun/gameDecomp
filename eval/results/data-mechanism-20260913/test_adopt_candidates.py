import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('adopt_candidates', Path(__file__).with_name('adopt_candidates.py'))
adopt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adopt)


def attempt(**changes):
    values = dict(compiled=True, frontend={'passed':True}, exact=True, verification={'exact':True})
    values.update(changes)
    return SimpleNamespace(**values)


def test_gates_require_exact_or_certified_boundary_plus_frontend():
    assert adopt.candidate_gate(attempt())
    assert adopt.candidate_gate(attempt(exact=False, verification={'function_boundary':{'function_exact':True}}))
    for changed in [dict(compiled=False), dict(frontend=None), dict(frontend={'passed':False}),
                    dict(exact=False), dict(verification={}), dict(verification={'exact':False})]:
        assert not adopt.candidate_gate(attempt(**changed))


def test_ratchet_binds_each_previously_verified_source_and_attempt():
    before={'f':('integrated','abc',1)}
    assert adopt.ratchet(before,{'f':dict(status='integrated',source_sha256='abc',attempt_id=1)}) is None
    for change in [dict(status='object_exact'),dict(source_sha256='xyz'),dict(attempt_id=2)]:
        with pytest.raises(ValueError):
            adopt.ratchet(before,{'f':dict(status='integrated',source_sha256='abc',attempt_id=1) | change})


def test_pause_requires_service_marker_no_worker_and_no_inflight(tmp_path):
    (tmp_path/'service.pause').touch()
    (tmp_path/'service.json').write_text(json.dumps({'status':'paused','worker_pid':None}))
    assert adopt.paused_drained(tmp_path,{}) is None
    for state in [{'inflight':{'f':1}}, {'fast_inflight':[1]}]:
        with pytest.raises(ValueError):adopt.paused_drained(tmp_path,state)
    (tmp_path/'service.json').write_text(json.dumps({'status':'paused','worker_pid':42}))
    with pytest.raises(ValueError):adopt.paused_drained(tmp_path,{})
    (tmp_path/'service.json').write_text(json.dumps({'status':'paused','worker_pid':None}))
    (tmp_path/'service.pause').unlink()
    with pytest.raises(ValueError):adopt.paused_drained(tmp_path,{})


def test_candidate_manifest_source_identity_and_bounded_unique_names(tmp_path):
    source=tmp_path/'source.c'
    source.write_text('int f(void) { return 0; }\n')
    row=dict(function='f',source=str(source),source_sha256=adopt.sha(source.read_text()),
             expected_parent_source_sha256='0'*64)
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps([row]))
    assert adopt.load_candidates(manifest)[0]['_source']==source.read_text()
    manifest.write_text(json.dumps([row,row]))
    with pytest.raises(ValueError):adopt.load_candidates(manifest)
    manifest.write_text(json.dumps([row]))
    source.write_text('changed')
    with pytest.raises(ValueError):adopt.load_candidates(manifest)
