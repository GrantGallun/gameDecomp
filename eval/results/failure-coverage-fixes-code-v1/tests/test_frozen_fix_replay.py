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
