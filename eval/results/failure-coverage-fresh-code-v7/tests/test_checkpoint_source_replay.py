import hashlib
import importlib
import json
import pytest

module=importlib.import_module('eval.experiments.campaign-gap-audit.replay_checkpoint_source')


def test_checkpoint_replay_requires_terminal_hash_bound_source(tmp_path):
    source=tmp_path/'candidate.c'
    code='void f(void) {}\n'
    source.write_text(code)
    checkpoint=tmp_path/'checkpoint.json'
    state={'status':'stalled_requires_new_strategy_or_evidence','nodes':{'f':{
        'source':str(source),'source_sha256':hashlib.sha256(code.encode()).hexdigest()}}}
    checkpoint.write_text(json.dumps(state))
    assert module.selected_source(checkpoint,'f')==code
    source.write_text(code+'// changed')
    with pytest.raises(ValueError,match='hash mismatch'):
        module.selected_source(checkpoint,'f')
    state['status']='running'
    checkpoint.write_text(json.dumps(state))
    with pytest.raises(ValueError,match='terminal checkpoint'):
        module.selected_source(checkpoint,'f')
