import copy
import sqlite3
from types import SimpleNamespace

import pytest

from eval import campaign_state, fast_campaign as fast
from test_campaign_fast import database


def test_worker_receives_only_relevant_capture_memory():
    config = {'model': 'unchanged', 'runtime_captures': {'a': [{'memory': [1]}], 'b': [{'memory': [2]}]}}
    selected = fast.worker_config(config, 'a')
    assert selected == {'model': 'unchanged', 'runtime_captures': {'a': [{'memory': [1]}]}}
    selected['runtime_captures']['a'][0]['memory'].append(3)
    assert config['runtime_captures']['a'][0]['memory'] == [1]
    assert fast.worker_config(config, 'other') == {'model': 'unchanged', 'runtime_captures': {}}


@pytest.mark.parametrize('legacy', [True, False])
def test_capture_cannot_be_enabled_without_amendment(legacy):
    options = dict(dispatch='pipeline', workers=1, model_parallel=1,
                   model_workers=1, tasks_per_worker=1, reasoned_effort='profile', integrate=False)
    state = {'runtime_options': dict(options)} if legacy else {}
    original = copy.deepcopy(state)
    with pytest.raises(ValueError, match='amendment'):
        fast.bind_runtime_options(state, {**options, 'runtime_plan': '/plans.json'})
    assert state == original
    fast.bind_runtime_options(state, {**options, 'runtime_plan': None})
    state['runtime_options']['runtime_plan'] = '/plans.json'
    fast.bind_runtime_options(state, {**options, 'runtime_plan': '/plans.json'})


@pytest.mark.parametrize('enabled,paused', [(True, False), (True, True), (False, False)])
def test_drained_controller_runs_capture_once_and_saves_progress(tmp_path, monkeypatch, enabled, paused):
    db = tmp_path/'db.sqlite'
    database(db)
    with sqlite3.connect(db) as conn:
        inventory = list(conn.execute('SELECT name,addr,size,insn_count FROM functions ORDER BY name'))
    repo = tmp_path/'repo'
    repo.mkdir()
    path = tmp_path/'campaign.json'
    plan = tmp_path/'plans.json' if enabled else None
    args = SimpleNamespace(resume=True, scheduler='evidence-v1', workers=1, state=path, repo=repo,
        db=db, project=tmp_path, model='fake', endpoint='fake', model_calls=0,
        timeout=10, num_predict=100, worker_root=tmp_path/'workers', max_work_items=0,
        dispatch='pipeline', model_parallel=1, integrate=False, runtime_plan=plan)
    config = {k: str(getattr(args, k)) if k in {'repo', 'db', 'project'} else getattr(args, k)
        for k in ('repo','db','project','model','endpoint','model_calls','timeout','num_predict','scheduler')}
    options = dict(dispatch='pipeline', workers=1, model_parallel=1, model_workers=1,
                   tasks_per_worker=1, reasoned_effort='profile', integrate=False,
                   runtime_plan=str(plan) if plan else None)
    campaign_state.atomic(path, dict(config=config, nodes={}, pins={}, runtime_options=options,
        inventory_sha256=fast.campaign.digest(inventory), model_digest=None))
    monkeypatch.setattr(fast.campaign, '_pins', lambda *args: {})
    monkeypatch.setattr(fast.campaign.frozen_wavefront, 'model_digest', lambda *args: None)
    calls = []
    def sweep(state, **kwargs):
        assert not state.get('fast_inflight')
        assert kwargs['plan_path'] == plan
        state['runtime_capture'] = {'status': 'capturing'}
        kwargs['on_progress']()
        assert campaign_state.read(path)['runtime_capture']['status'] == 'capturing'
        state['runtime_capture']['status'] = 'observed_pass'
        calls.append(kwargs['checkpoint'])
        return []
    monkeypatch.setattr(fast.campaign_runtime, 'sweep', sweep)
    if paused:
        (tmp_path/'service.pause').touch()
    fast.run(args)
    assert len(calls) == int(enabled and not paused)
    if calls:
        assert campaign_state.read(path)['runtime_capture']['status'] == 'observed_pass'
