"""The fast controller dispatches the exact profile selected by its scheduler."""

import hashlib
import os
import sqlite3
from types import SimpleNamespace

import pytest

from eval import campaign_state, fast_campaign
from solver import repair_queue


def test_pipeline_resource_filter_uses_investigation_profile(monkeypatch):
    investigation = {'name': 'investigate', 'model': True, 'lane': 'byte',
                     'evidence_key': 'evidence-a',
                     'investigation_policy': {'max_tool_calls': 3},
                     'capability_issues': {'shared': {'affected_functions': ['a', 'b']}}}
    cpu = {'name': 'recertify@sha', 'model': False, 'lane': 'byte',
           'evidence_key': 'evidence-b'}
    state = {'config': {'scheduler': 'investigation-v1', 'model_calls': 1},
             'nodes': {'a': {}, 'b': {}}, 'repair_queue': {'work_items': {
                 'a': {'function': 'a', 'priority': 1, 'profile': 'investigate',
                       'evidence_key': 'evidence-a'},
                 'b': {'function': 'b', 'priority': 0, 'profile': 'recertify@sha',
                       'evidence_key': 'evidence-b'}}}}
    monkeypatch.setattr(fast_campaign.campaign, 'scheduled_profile',
                        lambda state, node: investigation if node is state['nodes']['a'] else cpu)
    monkeypatch.setattr(fast_campaign.campaign.repair_queue, 'next_profile',
                        lambda *args: pytest.fail('bypassed scheduler-aware profile'))

    picked = fast_campaign.dispatch_items(state, [], 1, 1, 1, pipeline=True)
    assert [item['function'] for item in picked] == ['a']
    assert fast_campaign.dispatch_profile(state, picked[0]) == investigation


def test_capability_work_item_keeps_projected_task_and_priority(monkeypatch):
    task = {'evidence': {'kind': 'shared'}, 'hypothesis': 'test'}
    task_key = repair_queue.fingerprint(task)
    state = {'config': {'scheduler': 'investigation-v1', 'model_calls': 1},
             'nodes': {'a': {}, 'b': {}},
             'capability_tasks': {'issue': task},
             'repair_queue': {'shared_issues': {
                 'issue': {'identity': task['evidence'], 'affected_functions': ['a', 'b']}},
                 'work_items': {
                     'a': {'function': 'a', 'priority': 0, 'profile': 'capability_repair',
                           'evidence_key': task_key},
                     'b': {'function': 'b', 'priority': 1, 'profile': 'investigate',
                           'evidence_key': 'other'}}}}
    monkeypatch.setattr(fast_campaign.campaign, 'scheduled_profile',
                        lambda *args: {'name': 'investigate', 'model': True,
                                       'evidence_key': 'other'})

    picked = fast_campaign.dispatch_items(state, [], 1, 1, 1, pipeline=False)
    assert [item['function'] for item in picked] == ['a']
    assert fast_campaign.dispatch_profile(state, picked[0]) == {
        'name': 'capability_repair', 'model': True, 'capability_task': task,
        'issue_key': 'issue', 'lane': 'capability', 'evidence_key': task_key}


def test_capability_receipt_validates_task_identity_and_source(tmp_path):
    source = tmp_path / 'source.c'
    source.write_text('int f(void) { return 1; }')
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    task = {'evidence': {'kind': 'shared'}}
    task_key = repair_queue.fingerprint(task)
    node = {'source': str(source), 'source_sha256': source_hash,
            'jobs': [], 'status': 'pending'}
    job = {'node': dict(node), 'profile': {'name': 'capability_repair',
           'capability_task': task, 'evidence_key': task_key}}
    result = {'auxiliary': True, 'status': 'not_reproduced'}
    fast_campaign.validate_job(node, job, result)

    job['profile']['capability_task'] = {'evidence': {'kind': 'changed'}}
    with pytest.raises(ValueError, match='stale'):
        fast_campaign.validate_job(node, job, result)


def test_source_free_capability_receipt_keeps_node_identity():
    task = {'evidence': {'kind': 'shared'}}
    node = {'status': 'parked', 'blocker': {'status': 'backend_unavailable'}, 'jobs': []}
    job = {'node': dict(node), 'profile': {'name': 'capability_repair',
           'capability_task': task, 'evidence_key': repair_queue.fingerprint(task)}}
    report = {'auxiliary': True, 'status': 'not_reproduced'}
    fast_campaign.validate_job(node, job, report)

    node['source'] = 'new-source.c'
    with pytest.raises(ValueError, match='stale'):
        fast_campaign.validate_job(node, job, report)
    node.pop('source')
    node['blocker'] = {'status': 'different_evidence'}
    with pytest.raises(ValueError, match='stale'):
        fast_campaign.validate_job(node, job, report)


def test_source_free_capability_worker_exception_is_auxiliary(tmp_path, monkeypatch):
    import json
    import time

    job = {'repo': str(tmp_path), 'db': str(tmp_path / 'private.sqlite'),
           'function': 'f', 'node': {'status': 'parked'},
           'profile': {'name': 'capability_repair', 'capability_task': {'evidence': {}}},
           'config': {}, 'raw': str(tmp_path / 'raw.json')}
    monkeypatch.setattr(fast_campaign.campaign, 'execute',
                        lambda **kwargs: (_ for _ in ()).throw(OSError('backend unavailable')))
    original_connect = sqlite3.connect
    try:
        fast_campaign._execute_worker(job, {}, original_connect, time.time(), time.monotonic())
    finally:
        sqlite3.connect = original_connect
    result = json.loads((tmp_path / 'raw.json').read_text())
    assert result['auxiliary'] is True
    assert result['status'] == 'operational_failure'
    assert 'backend unavailable' in result['error']
    assert 'source' not in result


@pytest.mark.skipif(os.name == 'nt', reason='native controller uses flock and SQLite rename')
def test_controller_submits_scheduler_profile_to_worker(tmp_path, monkeypatch):
    from test_campaign_fast import append, database

    main = tmp_path / 'main.sqlite'
    database(main)
    with sqlite3.connect(main) as conn:
        inventory = list(conn.execute('SELECT name,addr,size,insn_count FROM functions ORDER BY name'))
    repo = tmp_path / 'repo'
    source = repo / 'nonmatchings' / 'f' / 'source.c'
    source.parent.mkdir(parents=True)
    source.write_text('int f(void) { return 0; }')
    node = {'status': 'pending', 'source': str(source),
            'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'attempt_id': 1, 'jobs': [], 'residual': {'compiled': True}}
    evidence = repair_queue.evidence_key(node)
    policy = {'max_tool_calls': 3, 'max_attempts': 2}
    issues = {'shared': {'affected_functions': ['f', 'g']}}
    selected_profile = {'name': 'investigate', 'model': True, 'lane': 'byte',
                        'evidence_key': evidence, 'investigation_policy': policy,
                        'capability_issues': issues}
    path = tmp_path / 'campaign.json'
    (tmp_path / 'campaign-artifacts').mkdir()
    args = SimpleNamespace(resume=True, scheduler='investigation-v1', workers=1,
        state=path, repo=repo, db=main, project=tmp_path, model='fake', endpoint='offline',
        model_calls=1, timeout=10, num_predict=100, worker_root=tmp_path / 'workers',
        max_work_items=1, dispatch='pipeline', model_workers=1, model_parallel=1,
        tasks_per_worker=1, reasoned_effort='profile', integrate=False,
        runtime_plan=None, deterministic_only=False)
    config = {key: str(getattr(args, key)) if key in {'repo', 'db', 'project'} else getattr(args, key)
              for key in ('repo', 'db', 'project', 'model', 'endpoint', 'model_calls',
                          'timeout', 'num_predict', 'scheduler')}
    options = {'dispatch': 'pipeline', 'workers': 1, 'model_parallel': 1,
               'model_workers': 1, 'tasks_per_worker': 1, 'reasoned_effort': 'profile',
               'integrate': False, 'runtime_plan': None}
    campaign_state.atomic(path, {'kind': 'resumable-completion-campaign',
        'config': config, 'nodes': {'f': node}, 'pins': {}, 'runtime_options': options,
        'inventory_sha256': fast_campaign.campaign.digest(inventory),
        'model_digest': 'pinned'})
    monkeypatch.setattr(fast_campaign.campaign, '_pins', lambda *args: {})
    monkeypatch.setattr(fast_campaign.campaign.frozen_wavefront, 'model_digest',
                        lambda *args: 'pinned')
    monkeypatch.setattr(fast_campaign.campaign, 'scheduled_profile',
                        lambda *args: selected_profile)
    def project(state):
        state['repair_queue'] = {'work_items': {'f': {'function': 'f',
            'priority': 0, 'profile': 'investigate', 'evidence_key': evidence}}
            if not state['nodes']['f']['jobs'] else {}}
        return ('f', selected_profile) if state['repair_queue']['work_items'] else None
    monkeypatch.setattr(fast_campaign, 'project', project)
    dispatched = []
    class Pool:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def submit(self, fn, job):
            dispatched.append(job['profile'])
            append(job['db'], job['id'])
            raw = {'source': job['node']['source'],
                   'source_sha256': job['node']['source_sha256'], 'attempt_id': 2,
                   'exact': False, 'score': 50., 'wall_seconds': 1.,
                   'residual': {'compiled': True, 'frontend': {'passed': True}},
                   'performance': {'model_seconds': 0., 'model_queue_seconds': 0.}}
            campaign_state.atomic(job['raw'], raw)
            from concurrent.futures import Future
            future = Future()
            future.set_result(job['raw'])
            return future
    monkeypatch.setattr(fast_campaign, 'ProcessPoolExecutor', Pool)

    fast_campaign.run(args)
    assert len(dispatched) == 1
    assert dispatched[0]['name'] == 'investigate'
    assert dispatched[0]['investigation_policy'] == policy
    assert dispatched[0]['capability_issues'] == issues


@pytest.mark.skipif(os.name == 'nt', reason='native controller uses flock and SQLite rename')
def test_controller_runs_source_free_capability_without_game_workspace(tmp_path, monkeypatch):
    from test_campaign_fast import database

    main = tmp_path / 'main.sqlite'
    database(main)
    with sqlite3.connect(main) as conn:
        inventory = list(conn.execute('SELECT name,addr,size,insn_count FROM functions ORDER BY name'))
    repo = tmp_path / 'repo'
    repo.mkdir()
    node = {'status': 'parked', 'blocker': {'status': 'backend_unavailable'}, 'jobs': []}
    task = {'evidence': {'kind': 'shared'}}
    task_key = repair_queue.fingerprint(task)
    path = tmp_path / 'campaign.json'
    (tmp_path / 'campaign-artifacts').mkdir()
    args = SimpleNamespace(resume=True, scheduler='investigation-v1', workers=1,
        state=path, repo=repo, db=main, project=tmp_path, model='fake', endpoint='offline',
        model_calls=1, timeout=10, num_predict=100, worker_root=tmp_path / 'workers',
        max_work_items=1, dispatch='pipeline', model_workers=1, model_parallel=1,
        tasks_per_worker=1, reasoned_effort='profile', integrate=False,
        runtime_plan=None, deterministic_only=False)
    config = {key: str(getattr(args, key)) if key in {'repo', 'db', 'project'} else getattr(args, key)
              for key in ('repo', 'db', 'project', 'model', 'endpoint', 'model_calls',
                          'timeout', 'num_predict', 'scheduler')}
    options = {'dispatch': 'pipeline', 'workers': 1, 'model_parallel': 1,
               'model_workers': 1, 'tasks_per_worker': 1, 'reasoned_effort': 'profile',
               'integrate': False, 'runtime_plan': None}
    campaign_state.atomic(path, {'kind': 'resumable-completion-campaign',
        'config': config, 'nodes': {'f': node}, 'pins': {}, 'runtime_options': options,
        'capability_tasks': {'issue': task},
        'inventory_sha256': fast_campaign.campaign.digest(inventory),
        'model_digest': 'pinned'})
    monkeypatch.setattr(fast_campaign.campaign, '_pins', lambda *args: {})
    monkeypatch.setattr(fast_campaign.campaign.frozen_wavefront, 'model_digest',
                        lambda *args: 'pinned')
    monkeypatch.setattr(fast_campaign.campaign_workers, 'isolate',
                        lambda *args: pytest.fail('capability job isolated absent game workspace'))
    def project(state):
        item = {'function': 'f', 'priority': 0, 'profile': 'capability_repair',
                'evidence_key': task_key}
        state['repair_queue'] = {'shared_issues': {'issue': {
            'identity': task['evidence'], 'affected_functions': ['f']}},
            'work_items': {'f': item} if not state['nodes']['f']['jobs'] else {}}
        return ('f', {}) if state['repair_queue']['work_items'] else None
    monkeypatch.setattr(fast_campaign, 'project', project)
    dispatched = []
    class Pool:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def submit(self, fn, job):
            dispatched.append(job)
            raw = {'auxiliary': True, 'status': 'operational_failure',
                   'error': 'reproduction unavailable', 'wall_seconds': 1.,
                   'performance': {'model_seconds': 0., 'model_queue_seconds': 0.}}
            campaign_state.atomic(job['raw'], raw)
            from concurrent.futures import Future
            future = Future()
            future.set_result(job['raw'])
            return future
    monkeypatch.setattr(fast_campaign, 'ProcessPoolExecutor', Pool)

    result = fast_campaign.run(args)
    assert len(dispatched) == 1
    assert dispatched[0]['repo'] == str(repo)
    assert dispatched[0]['profile']['capability_task'] == task
    assert 'source' not in result['nodes']['f']
    assert result['nodes']['f']['status'] == 'parked'
    assert result['nodes']['f']['capability_experiments'][0]['status'] == 'operational_failure'
