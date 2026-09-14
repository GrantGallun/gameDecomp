"""Controller integration reconciliation: gates, freshness, union and retry keys."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from eval import campaign_integration as integration


@pytest.fixture
def harness(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'snowboardkids.z64').write_bytes(b'ROM')
    artifacts = tmp_path / 'campaign-artifacts'
    nodes = {}
    for name in ('f', 'g', 'h', 'i', 'j', 'k'):
        source = tmp_path / (name + '.c')
        source.write_text('int ' + name + '(void) {return 0;}')
        nodes[name] = {'source': str(source), 'source_sha256': integration.file_digest(source),
                       'attempt_id': ord(name), 'status': 'function_exact_pending_integration',
                       'verification': {'exact': False}}
    state = {'nodes': nodes, 'pins': {}}
    calls = []
    def preflight(**kwargs):
        return kwargs['entries'], []
    def build(**kwargs):
        batch = kwargs['entries']
        calls.append([e['function'] for e in batch])
        folder = kwargs['artifacts']
        prepared = folder / 'test-prepared'
        prepared.mkdir()
        manifest = {'reference_rom': 'snowboardkids.z64',
                    'reference_sha256': integration.file_digest(repo / 'snowboardkids.z64'),
                    'replacements': [],
                    'lineage': [{**e, 'source_sha256': integration.file_digest(e['source'])} for e in batch]}
        path = prepared / 'manifest.json'
        path.write_text(json.dumps(manifest))
        archive = folder / 'test.rebuilt.z64'
        archive.write_bytes(b'ROM')
        receipt = {'status': 'rom_exact', 'whole_rom_verified': True,
                   'manifest_sha256': integration.file_digest(path),
                   'target_sha256': integration.file_digest(archive), 'candidate_sha256': integration.file_digest(archive),
                   'target_bytes': 3, 'candidate_bytes': 3, 'first_difference_offset': None,
                   'built_rom_artifact': str(archive)}
        receipt_path = folder / 'test-integration.json'
        receipt_path.write_text(json.dumps(receipt))
        return batch, [{'status': 'rom_exact', 'receipt': str(receipt_path), 'functions': calls[-1]}]
    monkeypatch.setattr(integration.campaign, 'preflight_integration', preflight)
    monkeypatch.setattr(integration.campaign, 'integrate_candidates', build)
    def check_pins(pins):
        if any(integration.file_digest(p) != h for p, h in pins.items()):
            raise ValueError('frozen input changed')
    monkeypatch.setattr(integration.campaign.frozen_wavefront, 'verify_files', check_pins)
    return state, dict(repo=repo, db=tmp_path/'db', artifacts=artifacts, checkpoint=7), calls, build


def test_bounded_success_and_cumulative_union(harness):
    state, args, calls, _ = harness
    assert integration.sweep(state, **args) == list('fghij')
    assert state['nodes']['k']['status'] == 'function_exact_pending_integration'
    assert integration.sweep(state, **args) == ['k']
    assert calls == [list('fghij'), list('fghijk')]
    history = state['integration_sweep']
    assert history['verified_union'] == list('fghijk')
    assert history['latest']['checkpoint'] == 7
    assert all(not Path(a['path']).is_absolute() for a in history['latest']['artifacts'])
    assert integration.sweep(state, **args) == []
    assert len(calls) == 2


def test_unchanged_failures_suppressed_without_starving_later_pending(harness, monkeypatch):
    state, args, calls, _ = harness
    def fail(**kwargs):
        calls.append([e['function'] for e in kwargs['entries']])
        return [], [{'status': 'build_failed'}]
    monkeypatch.setattr(integration.campaign, 'integrate_candidates', fail)
    for _ in range(3):
        assert integration.sweep(state, **args) == []
    assert calls == [list('fghij'), ['k']]
    state['nodes']['f']['verification']['new_evidence'] = True
    integration.sweep(state, **args)
    assert calls[-1] == ['f']
    assert all(n['status'] == 'function_exact_pending_integration' for n in state['nodes'].values())


@pytest.mark.parametrize('tamper', ['source', 'node', 'receipt', 'manifest', 'archive', 'pin', 'missing_receipt', 'json_receipt'])
def test_changed_inputs_or_artifacts_never_promote(harness, monkeypatch, tamper):
    state, args, calls, build = harness
    if tamper == 'pin':
        path = args['repo'] / 'header.h'
        path.write_text('original')
        state['pins'][str(path)] = integration.file_digest(path)
    def changed(**kwargs):
        survivors, records = build(**kwargs)
        folder = kwargs['artifacts']
        if tamper == 'source':
            Path(state['nodes']['f']['source']).write_text('changed')
        elif tamper == 'node':
            state['nodes']['f']['attempt_id'] += 1
        elif tamper == 'receipt':
            path = Path(records[0]['receipt'])
            receipt = json.loads(path.read_text())
            receipt['whole_rom_verified'] = False
            path.write_text(json.dumps(receipt))
        elif tamper == 'manifest':
            (folder / 'test-prepared/manifest.json').write_text('{}')
        elif tamper == 'archive':
            (folder / 'test.rebuilt.z64').write_bytes(b'BAD')
        elif tamper == 'pin':
            (args['repo'] / 'header.h').write_text('changed')
        elif tamper == 'missing_receipt':
            Path(records[0]['receipt']).unlink()
        elif tamper == 'json_receipt':
            Path(records[0]['receipt']).write_text('[]')
        return survivors, records
    monkeypatch.setattr(integration.campaign, 'integrate_candidates', changed)
    assert integration.sweep(state, **args) == []
    assert state['integration_sweep']['latest']['status'] == 'stale'
    assert all(n['status'] == 'function_exact_pending_integration' for n in state['nodes'].values())


def test_old_union_preflight_failure_blocks_new_build(harness, monkeypatch):
    state, args, calls, _ = harness
    state['nodes']['f']['status'] = 'integrated'
    monkeypatch.setattr(integration.campaign, 'preflight_integration', lambda **kw:
                        ([e for e in kw['entries'] if e['function'] != 'f'],
                         [{'status': 'preparation_blocked', 'functions': ['f']}]))
    assert integration.sweep(state, **args) == []
    assert calls == []
    assert state['nodes']['f']['status'] == 'integrated'
    assert state['integration_sweep']['latest']['status'] == 'integration_halted'


def test_partial_old_union_failure_never_promotes_new_survivors(harness, monkeypatch):
    state, args, _, _ = harness
    state['nodes']['f']['status'] = 'integrated'
    monkeypatch.setattr(integration.campaign, 'integrate_candidates', lambda **kw:
                        ([e for e in kw['entries'] if e['function'] != 'f'], []))
    assert integration.sweep(state, **args) == []
    assert state['nodes']['g']['status'] == 'function_exact_pending_integration'
    assert state['integration_sweep']['latest']['status'] == 'integration_halted'


def test_prior_success_binding_change_halts_and_preserves_history(harness):
    state, args, calls, _ = harness
    integration.sweep(state, **args)
    previous = copy.deepcopy(state['integration_sweep']['latest_success'])
    state['nodes']['f']['attempt_id'] += 1
    assert integration.sweep(state, **args) == []
    assert len(calls) == 1
    assert state['integration_sweep']['latest_success'] == previous
    assert state['integration_sweep']['verified_union'] == list('fghij')


def test_requires_drained_workers_and_ignores_object_exact(harness):
    state, args, calls, _ = harness
    state['fast_inflight'] = [{}]
    with pytest.raises(ValueError, match='drained'):
        integration.sweep(state, **args)
    state['fast_inflight'] = []
    for node in state['nodes'].values():
        node['status'] = 'object_exact'
    assert integration.sweep(state, **args) == []
    assert calls == []


def test_running_progress_saved_without_promoting_nodes(harness):
    state, args, _, _ = harness
    observed = []
    def progress():
        observed.append(copy.deepcopy(state['integration_sweep']['latest']))
        assert all(n['status'] == 'function_exact_pending_integration' for n in state['nodes'].values())
    integration.sweep(state, **args, on_started=progress)
    assert len(observed) == 1 and observed[0]['status'] == 'running'


@pytest.mark.parametrize('paused', [False, True])
def test_controller_zero_work_drained_sweep_and_pause(tmp_path, monkeypatch, paused):
    from types import SimpleNamespace
    import sqlite3
    from eval import campaign_state, fast_campaign as fast
    from test_campaign_fast import database
    db = tmp_path / 'db.sqlite'
    database(db)
    with sqlite3.connect(db) as conn:
        inventory = list(conn.execute('SELECT name,addr,size,insn_count FROM functions ORDER BY name'))
    repo = tmp_path / 'repo'
    repo.mkdir()
    path = tmp_path / 'campaign.json'
    args = SimpleNamespace(resume=True, scheduler='evidence-v1', workers=1, state=path, repo=repo,
                           db=db, project=tmp_path, model='fake', endpoint='fake', model_calls=0,
                           timeout=10, num_predict=100, worker_root=tmp_path/'workers', max_work_items=0,
                           dispatch='pipeline', model_parallel=1, integrate=True)
    config = {k: str(getattr(args, k)) if k in {'repo', 'db', 'project'} else getattr(args, k)
              for k in ('repo', 'db', 'project', 'model', 'endpoint', 'model_calls', 'timeout', 'num_predict', 'scheduler')}
    runtime = {'dispatch': 'pipeline', 'workers': 1, 'model_parallel': 1, 'model_workers': 1,
               'tasks_per_worker': 1, 'reasoned_effort': 'profile', 'integrate': True}
    campaign_state.atomic(path, {'config': config, 'nodes': {}, 'pins': {}, 'runtime_options': runtime,
                                'inventory_sha256': fast.campaign.digest(inventory), 'model_digest': None})
    monkeypatch.setattr(fast.campaign, '_pins', lambda *a: {})
    monkeypatch.setattr(fast.campaign.frozen_wavefront, 'model_digest', lambda *a: None)
    calls = []
    def sweep(state, **kw):
        assert not state.get('fast_inflight')
        assert path.with_suffix('.lock').exists()
        calls.append(kw['checkpoint'])
        return []
    monkeypatch.setattr(fast.campaign_integration, 'sweep', sweep)
    if paused:
        (tmp_path / 'service.pause').touch()
    fast.run(args)
    assert len(calls) == (0 if paused else 1)
