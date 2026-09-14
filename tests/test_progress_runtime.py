import copy
import hashlib
import json

import pytest

from eval.campaign_state import Store
from eval.progress_map import MapFeed
from eval import progress_runtime
from solver.evidence_schedule import fingerprint


def fixture(tmp_path, status='passed'):
    node = {'source_sha256': 'a' * 64, 'attempt_id': 7,
            'verification': {'source': 'a'}, 'status': 'pending', 'size': 32}
    binding = {'source_sha256': node['source_sha256'], 'attempt_id': 7,
               'verification_sha256': fingerprint(node['verification'])}
    path = tmp_path / 'capture-job.json'
    path.write_text(json.dumps({'status': status, 'authoritative': False}))
    latest = {'status': status, 'function': 'leaf', 'plan_id': 'configured-leaf',
              'checkpoint': 4, 'source_binding': binding, 'capture_count': 1,
              'counts': {'passed': 1, 'failed': 0, 'inconclusive': 0},
              'started_at': 100, 'updated_at': 110,
              'artifacts': [{'kind': 'receipt', 'path': path.name,
                            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]}
    return node, binding, {'schema_version': 1, 'latest': latest}


@pytest.mark.parametrize('status', ['capturing', 'replaying', 'passed', 'failed', 'unavailable'])
def test_phase_and_function_are_explicit_without_exactness_promotion(tmp_path, status):
    node, _, runtime = fixture(tmp_path, status)
    Store(tmp_path / 'campaign.json').save({'nodes': {'leaf': node}, 'runtime_capture': runtime})
    feed = MapFeed(tmp_path)
    result = feed.runtime()
    assert result['status'] == status and result['function'] == 'leaf'
    assert result['capture_count'] == 1 and result['authoritative'] is False
    assert result['current_pass'] == (status == 'passed')
    assert feed.get()['exact_bytes'] == 0 and feed.get('leaf')['status'] == 'pending'
    artifact = result['artifacts'][0]
    assert artifact['url'].startswith('/api/runtime-artifact?sha256=')
    raw, kind = feed.runtime_artifact(artifact['sha256'])
    assert kind == 'receipt' and hashlib.sha256(raw).hexdigest() == artifact['sha256']
    with pytest.raises(KeyError):
        feed.runtime_artifact('unrecorded')


@pytest.mark.parametrize('field', ['source_sha256', 'attempt_id', 'verification_sha256'])
def test_stale_source_keeps_observation_historical(tmp_path, field):
    _, binding, runtime = fixture(tmp_path)
    current = copy.deepcopy(binding)
    current[field] = 'changed'
    result = progress_runtime.project(runtime, {'leaf': current}, tmp_path, 8)
    assert result['status'] == 'passed' and result['counts']['passed'] == 1
    assert not result['current_pass'] and not result['current_binding']
    assert any('Current source' in issue for issue in result['issues'])


def test_receipt_tamper_blocks_current_pass_and_download(tmp_path):
    node, binding, runtime = fixture(tmp_path)
    Store(tmp_path / 'campaign.json').save({'nodes': {'leaf': node}, 'runtime_capture': runtime})
    feed = MapFeed(tmp_path)
    (tmp_path / 'capture-job.json').write_text('{}')
    result = feed.runtime()
    assert not result['current_pass'] and not result['artifacts'][0]['available']
    with pytest.raises(ValueError, match='checksum'):
        feed.runtime_artifact(runtime['latest']['artifacts'][0]['sha256'])


def test_error_is_visible_and_bounded(tmp_path):
    _, binding, runtime = fixture(tmp_path, 'unavailable')
    runtime['latest']['error'] = 'Emulator timeout: ' + 'x' * 3000
    result = progress_runtime.project(runtime, {'leaf': binding}, tmp_path, 8)
    assert result['error'].startswith('Emulator timeout') and len(result['error']) == 2000
    assert not result['current_pass']


def test_no_capture_or_inconclusive_counts_cannot_claim_current_pass(tmp_path):
    _, binding, runtime = fixture(tmp_path)
    runtime['latest']['capture_count'] = 0
    assert not progress_runtime.project(runtime, {'leaf': binding}, tmp_path, 8)['current_pass']
    runtime['latest']['capture_count'] = 1
    runtime['latest']['counts']['inconclusive'] = 1
    assert not progress_runtime.project(runtime, {'leaf': binding}, tmp_path, 8)['current_pass']


def test_runtime_artifact_cannot_read_unrecorded_or_outside_paths(tmp_path):
    _, binding, runtime = fixture(tmp_path)
    runtime['latest']['artifacts'][0]['path'] = '../other.json'
    result = progress_runtime.project(runtime, {'leaf': binding}, tmp_path, 8)
    assert not result['current_pass'] and not result['artifacts'][0]['available']


def test_missing_job_stays_waiting(tmp_path):
    result = progress_runtime.project(None, {}, tmp_path, 8)
    assert result['status'] == 'not_run' and result['capture_count'] == 0
