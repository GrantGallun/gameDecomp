import hashlib
from pathlib import Path

import pytest

from eval import completion_campaign as campaign
from solver import repair_queue


@pytest.mark.parametrize('helper', ['c89.py', 'repair_context.py'])
def test_operand_revision_changes_with_direct_generator_helper(monkeypatch, helper):
    read_bytes = Path.read_bytes
    monkeypatch.setattr(repair_queue, '_OPERAND_REPAIR_DIGEST', None)
    before = repair_queue.operand_repair_digest()
    monkeypatch.setattr(Path, 'read_bytes', lambda path: read_bytes(path) +
                        (b'\n# changed generator dependency\n' if path.name == helper else b''))
    monkeypatch.setattr(repair_queue, '_OPERAND_REPAIR_DIGEST', None)
    assert repair_queue.operand_repair_digest() != before


def near_node(tmp_path):
    path = tmp_path / 'f.c'
    path.write_text('void f(void) { int value; value = 1; }\n')
    return {'status': 'pending', 'source': str(path),
            'source_sha256': hashlib.sha256(path.read_text().encode()).hexdigest(),
            'attempt_id': 42, 'score': 97.778, 'jobs': [],
            'residual': {'compiled': True, 'frontend': {'passed': True},
                         'faults': {'structural': 1, 'register_allocation': 0}},
            'semantic_validation': {'status': 'unavailable', 'reason': 'hardware'}}


def test_nonregister_nearmiss_gets_one_operand_visit_before_redrafting(tmp_path, monkeypatch):
    node = near_node(tmp_path)
    key = repair_queue.evidence_key(node)
    node['jobs'] = [{'profile': p, 'evidence_key': key, 'source_sha256': node['source_sha256']}
                    for p in ('local_rewrites', 'deeper_composition')]
    monkeypatch.setattr(repair_queue, 'operand_repair_digest', lambda: 'v1', raising=False)
    profile = repair_queue.next_profile(node, 3, campaign.PROFILES, 'binary-v1')
    assert profile['name'] == 'operand_repair@v1'
    assert profile['operand_repair'] is True and profile['model'] is False
    assert profile['operand_budget'] == 72
    node['jobs'].append({'profile': profile['name'], 'source_sha256': node['source_sha256']})
    assert repair_queue.next_profile(node, 3, campaign.PROFILES, 'binary-v1')['name'] == 'binary_types@binary-v1'
    # A different incumbent does not erase the exhausted generator visit.
    original_sha = node['source_sha256']
    node['source_sha256'] = 'another-source'
    assert not repair_queue.next_profile(node, 3, campaign.PROFILES, 'binary-v1').get('operand_repair')
    node['source_sha256'] = original_sha
    monkeypatch.setattr(repair_queue, 'operand_repair_digest', lambda: 'v2')
    assert repair_queue.next_profile(node, 3, campaign.PROFILES, 'binary-v1')['name'] == 'operand_repair@v2'


@pytest.mark.parametrize('change', [
    {'status': 'object_exact'}, {'status': 'integrated'},
    {'status': 'function_exact_pending_integration'}, {'status': 'parked'},
    {'residual': {'compiled': False, 'frontend': {'passed': True}, 'faults': {'structural': 1}}},
    {'residual': {'compiled': True, 'frontend': {'passed': False}, 'faults': {'structural': 1}}},
    {'residual': {'compiled': True, 'frontend': {'passed': True}, 'faults': {'structural': 3}}},
    {'residual': {'compiled': True, 'frontend': {'passed': True}}},
])
def test_operand_visit_requires_observed_nearmiss_and_open_node(tmp_path, monkeypatch, change):
    node = near_node(tmp_path)
    node.update(change)
    monkeypatch.setattr(repair_queue, 'operand_repair_digest', lambda: 'v1', raising=False)
    profile = repair_queue.next_profile(node, 3, campaign.PROFILES, 'binary-v1')
    assert profile is None or not profile.get('operand_repair')


def test_cheap_recertification_precedes_operand_search(tmp_path, monkeypatch):
    node = near_node(tmp_path)
    node['score'] = 100.0
    monkeypatch.setattr(repair_queue, 'operand_repair_digest', lambda: 'v1', raising=False)
    assert repair_queue.next_profile(node, 3, campaign.PROFILES)['name'].startswith('recertify@')


def test_controller_dispatches_operand_route_with_bound_and_identity(tmp_path, monkeypatch):
    from eval import operand_repair
    node = near_node(tmp_path)
    calls = []
    monkeypatch.setattr(repair_queue, 'operand_repair_digest', lambda: 'v1')
    monkeypatch.setattr(operand_repair, 'run', lambda **kwargs: calls.append(kwargs) or {'exact': False})
    profile = {'name': 'operand_repair@v1', 'operand_repair': True,
               'operand_revision': 'v1', 'operand_budget': 24, 'model': False}
    result = campaign.execute(repo=tmp_path, db=tmp_path / 'db', function='f', node=node,
                              profile=profile, config={}, out=tmp_path / 'receipt.json')
    assert result == {'exact': False}
    assert len(calls) == 1 and calls[0]['budget'] == 24
    assert calls[0]['node']['attempt_id'] == 42
    assert calls[0]['node']['operand_revision'] == 'v1'
    assert 'operand_revision' not in node
    monkeypatch.setattr(repair_queue, 'operand_repair_digest', lambda: 'v2')
    with pytest.raises(ValueError, match='operand repair.*changed'):
        campaign.execute(repo=tmp_path, db=tmp_path / 'db', function='f', node=node,
                         profile=profile, config={}, out=tmp_path / 'receipt.json')
    assert len(calls) == 1


def test_new_operand_visit_runs_ahead_of_old_exhaustion_band(tmp_path, monkeypatch):
    node = near_node(tmp_path)
    node['jobs'] = [{'profile': p, 'source_sha256': node['source_sha256'],
                     'evidence_key': repair_queue.evidence_key(node)}
                    for p in ('local_rewrites', 'deeper_composition')]
    node['jobs'] += [{'profile': 'old'}] * 12
    fresh = {**node, 'jobs': [], 'residual': {**node['residual'], 'faults': {'structural': 8}}}
    monkeypatch.setattr(repair_queue, 'operand_repair_digest', lambda: 'v1')
    state = {'config': {'model_calls': 3}, 'nodes': {'near': node, 'fresh': fresh}}
    queue, selected = repair_queue.project(state, campaign.PROFILES)
    assert selected[0] == 'near'
    assert queue['work_items']['near']['priority'][0] == -1
    node['jobs'].append({'profile': 'operand_repair@v1'})
    assert repair_queue.project(state, campaign.PROFILES)[1][0] == 'fresh'


def test_campaign_pins_the_link_map_consumed_by_operand_repair(tmp_path, monkeypatch):
    from solver import binary_type_draft
    link_map = tmp_path / 'build/snowboardkids.map'
    link_map.parent.mkdir()
    link_map.write_text('target addresses')
    monkeypatch.setattr(campaign.frozen_wavefront, 'code_paths', lambda project: [])
    monkeypatch.setattr(binary_type_draft, 'input_paths', lambda repo: {})
    monkeypatch.setattr(campaign.frozen_wavefront, 'file_hashes',
                        lambda paths: {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                       for path in paths if path.is_file()})
    before = campaign._pins(tmp_path, tmp_path)
    assert str(link_map) in before
    link_map.write_text('changed addresses')
    assert campaign._pins(tmp_path, tmp_path)[str(link_map)] != before[str(link_map)]
