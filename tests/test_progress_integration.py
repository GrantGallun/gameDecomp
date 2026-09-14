import copy
import hashlib
import json

import pytest

from eval.campaign_state import Store
from eval.progress_map import MapFeed
from eval import progress_integration as view
from solver.evidence_schedule import fingerprint


def fixture(tmp_path):
    nodes = {name: {'status': 'integrated', 'source_sha256': name * 64,
                   'attempt_id': i, 'verification': {'source': name}, 'size': 4}
             for i, name in enumerate(('a', 'b'), 1)}
    bindings = {name: {'source_sha256': row['source_sha256'],
        'attempt_id': row['attempt_id'], 'verification_sha256': fingerprint(row['verification'])}
        for name, row in nodes.items()}
    path = tmp_path / 'receipt.json'
    path.write_text(json.dumps({'status': 'rom_exact'}))
    artifact = {'kind': 'receipt', 'path': path.name,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    latest = {'status': 'rom_exact', 'checkpoint': 4, 'prior_union': ['a'],
        'selected': ['b'], 'verified_union': ['a', 'b'],
        'source_bindings': bindings, 'artifacts': [artifact]}
    sweep = {'schema_version': 1, 'verified_union': ['a', 'b'], 'latest': latest}
    return nodes, bindings, sweep


def test_source_bound_current_receipt_does_not_grant_object_exactness(tmp_path):
    nodes, bindings, sweep = fixture(tmp_path)
    store = Store(tmp_path / 'campaign.json')
    store.save({'nodes': nodes, 'integration_sweep': sweep})
    feed = MapFeed(tmp_path)
    assert feed.get()['exact_bytes'] == 0
    assert feed.get('a')['category'] == 'rom_verified'
    value = feed.integration()
    assert value['current_binding'] and len(value['verified_union']) == 2
    assert value['complete_c_decompilation'] is False
    digest = value['artifacts'][0]['sha256']
    raw, kind = feed.integration_artifact(digest)
    assert kind == 'receipt' and json.loads(raw)['status'] == 'rom_exact'
    with pytest.raises(KeyError):
        feed.integration_artifact('unrecorded')


@pytest.mark.parametrize('key,value', [('source_sha256', 'changed'),
    ('attempt_id', 999), ('verification_sha256', 'changed')])
def test_changed_source_or_certificate_keeps_historical_receipt_only(tmp_path, key, value):
    _, bindings, sweep = fixture(tmp_path)
    current = copy.deepcopy(bindings)
    current['b'][key] = value
    result = view.project(sweep, current, tmp_path, 99)
    assert result['status'] == 'rom_exact'
    assert not result['current_binding']
    assert any('differs: b' in issue for issue in result['issues'])
    assert result['artifacts'][0]['available']


def test_partial_success_retains_prior_union_but_need_not_include_failed_selection(tmp_path):
    _, bindings, sweep = fixture(tmp_path)
    sweep['latest']['selected'].append('blocked')
    result = view.project(sweep, bindings, tmp_path, 5)
    assert result['current_binding']
    assert result['unverified_selection'] == ['blocked']
    sweep['latest']['verified_union'] = sweep['verified_union'] = ['b']
    assert not view.project(sweep, bindings, tmp_path, 5)['current_binding']


def test_missing_or_tampered_receipt_cannot_display_current_success(tmp_path):
    _, bindings, sweep = fixture(tmp_path)
    (tmp_path / 'receipt.json').write_text('{}')
    result = view.project(sweep, bindings, tmp_path, 5)
    assert not result['current_binding'] and not result['artifacts'][0]['available']
    assert 'checksum' in result['issues'][0]
    with pytest.raises(ValueError, match='checksum'):
        view.read_artifact(tmp_path, sweep['latest']['artifacts'][0])


@pytest.mark.parametrize('path', ['../secret', '/tmp/secret', 'C:/secret', r'..\secret', r'\\server\secret'])
def test_artifact_paths_never_expand_dashboard_file_access(tmp_path, path):
    with pytest.raises(ValueError):
        view.artifact_path(tmp_path, path)


def test_rom_is_not_a_downloadable_dashboard_artifact(tmp_path):
    with pytest.raises(ValueError, match='only integration receipts'):
        view.read_artifact(tmp_path, {'kind': 'rom', 'path': 'rom.z64'})


def test_new_checkpoint_invalidates_current_source_binding(tmp_path):
    nodes, _, sweep = fixture(tmp_path)
    store = Store(tmp_path / 'campaign.json')
    state = {'nodes': nodes, 'integration_sweep': sweep}
    store.save(state)
    feed = MapFeed(tmp_path)
    assert feed.integration()['current_binding']
    nodes['b']['source_sha256'] = 'changed'
    store.save(state, changed=['b'])
    feed.last = 0
    assert not feed.integration()['current_binding']


def test_empty_sweep_is_explicitly_not_run(tmp_path):
    assert view.project(None, {}, tmp_path, 1)['status'] == 'not_run'


def test_identical_artifacts_at_two_recorded_paths_remain_downloadable(tmp_path):
    nodes, _, sweep = fixture(tmp_path)
    original = sweep['latest']['artifacts'][0]
    (tmp_path / 'same-receipt.json').write_bytes((tmp_path / original['path']).read_bytes())
    sweep['latest']['artifacts'].append({**original, 'path': 'same-receipt.json'})
    Store(tmp_path / 'campaign.json').save({'nodes': nodes, 'integration_sweep': sweep})
    raw, _ = MapFeed(tmp_path).integration_artifact(original['sha256'])
    assert hashlib.sha256(raw).hexdigest() == original['sha256']


def test_later_failure_retains_prior_success_as_separate_bound_history(tmp_path):
    nodes, bindings, sweep = fixture(tmp_path)
    sweep['latest_success'] = copy.deepcopy(sweep['latest'])
    sweep['latest'] = {'status': 'preparation_blocked', 'checkpoint': 9,
        'selected': ['c'], 'prior_union': ['a', 'b'], 'verified_union': [],
        'source_bindings': {}, 'artifacts': []}
    result = view.project(sweep, bindings, tmp_path, 10)
    assert not result['current_binding'] and result['status'] == 'preparation_blocked'
    assert result['previous_success']['current_binding']
    store = Store(tmp_path / 'campaign.json')
    store.save({'nodes': nodes, 'integration_sweep': sweep})
    feed = MapFeed(tmp_path)
    artifact = sweep['latest_success']['artifacts'][0]
    assert feed.integration_artifact(artifact['sha256'])[1] == 'receipt'
    (tmp_path / 'receipt.json').write_text('{}')
    assert not feed.integration()['previous_success']['current_binding']
