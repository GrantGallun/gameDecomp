import copy
import json
from pathlib import Path
import sqlite3

import pytest

from eval import completion_campaign as campaign
from solver import repair_queue as queue


def node(semantic=None, **kw):
    return {'status': 'pending', 'jobs': [], 'source_sha256': 'source',
            'instruction_count': 20, 'dag_level': 0,
            'residual': {'compiled': True, 'frontend': {'passed': True}},
            'semantic_validation': semantic, **kw}


def state(nodes, **config):
    return {'config': {'scheduler': 'evidence-v1', 'model_calls': 2, **config}, 'nodes': nodes}


@pytest.mark.parametrize('semantic,expected', [
    (None, 'semantic_validate'),
    ({'status': 'observed_failure', 'counts': {'failed': 1}}, 'semantic_counterexample'),
    ({'status': 'observed_pass_with_execution_debt'}, 'local_rewrites'),
    ({'status': 'unavailable', 'reason': 'hardware'}, 'local_rewrites'),
])
def test_route_by_evidence(semantic, expected):
    n = node(semantic)
    assert campaign.choose(state({'f': n}))[1]['name'] == expected


def test_retry_requires_new_measured_evidence_not_logging_changes():
    n = node({'status': 'observed_failure', 'counts': {'failed': 1}})
    s = state({'f': n})
    for _ in range(2):
        _, p = campaign.choose(s)
        campaign.accept(n, p, {'semantic_validation': n['semantic_validation']}, Path('r.json'))
    assert campaign.choose(s) is None
    n.update(score=99, last_outcome={'wall_seconds': 100})
    assert campaign.choose(s) is None
    n['semantic_validation']['counts']['failed'] = 2
    assert campaign.choose(s)[1]['name'] == 'semantic_counterexample'


def test_unavailable_is_shared_issue_not_repeated_model_work():
    s = state({n: node({'status': 'unavailable', 'reason': 'PI registers'}) for n in ('a', 'b')})
    while (selected := campaign.choose(s)):
        name, p = selected
        assert p['model'] is False
        campaign.accept(s['nodes'][name], p,
                        {'semantic_validation': s['nodes'][name]['semantic_validation']}, Path('r.json'))
    projection, selected = queue.project(s, campaign.PROFILES)
    assert selected is None
    issue, = projection['shared_issues'].values()
    assert issue['affected_functions'] == ('a', 'b')
    assert issue['executable'] is False
    assert campaign.status(s) == 'stalled_requires_new_strategy_or_evidence'


def test_backend_identity_and_no_false_merging():
    def blocked(target):
        return node(status='parked', blocker={'status': 'object_postprocessing_backend_required',
                    'evidence': {'target': target, 'postprocess': 'trim', 'makefile_sha256': 'm'}})
    issues = queue.shared_issues({'a': blocked('tu1'), 'b': blocked('tu1'), 'c': blocked('tu2')})
    assert sorted(len(i['affected_functions']) for i in issues.values()) == [1, 2]


def test_graph_cycles_and_caller_leverage_without_dependency_gate():
    g = queue.graph({'a': {'b'}, 'b': {'a'}, 'c': {'a'}}, ['a', 'b', 'c'])
    assert ('a', 'b') in g['components']
    s = state({n: node({'status': 'observed_pass'}) for n in ('a', 'b', 'c')})
    s['dependency_graph'] = g
    assert campaign.choose(s)[0] == 'a'
    assert set(queue.project(s, campaign.PROFILES)[0]['work_items']) == {'a', 'b', 'c'}


def test_fairness_bands_and_compile_sweep():
    failed = node(residual={'compiled': False, 'frontend': {'passed': False}})
    failed['jobs'] = [{'profile': 'other'}] * 4
    s = state({'frontend': failed, 'byte': node({'status': 'observed_pass'})})
    assert campaign.choose(s)[0] == 'byte'
    s['config']['compile_sweep'] = True
    assert campaign.choose(s)[0] == 'frontend'


def test_projection_is_pure_and_json_roundtrip_stable():
    s = state({'f': node({'status': 'observed_failure'})})
    before = copy.deepcopy(s)
    p, chosen = queue.project(s, campaign.PROFILES)
    assert s == before
    s['repair_queue'] = p
    assert campaign.choose(json.loads(json.dumps(s))) == chosen


def test_campaign_evidence_resume_and_durable_result(tmp_path, monkeypatch):
    db = tmp_path / 'db.sqlite'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE functions(name,addr,size,insn_count)')
        c.execute("INSERT INTO functions VALUES('f',4096,16,4)")
    monkeypatch.setattr(campaign.callgraph, 'edges', lambda c: ({}, {}))
    monkeypatch.setattr(campaign, '_pins', lambda *a: {})
    kwargs = dict(repo=tmp_path, project=tmp_path, db=db, state_path=tmp_path / 's.json',
                  functions=('f',), model_calls=0, scheduler='evidence-v1')
    s = campaign.run(**kwargs, max_work_items=0)
    assert s['repair_queue']['policy'] == 'evidence-v1'
    receipt = tmp_path / 'r.json'
    receipt.write_text(json.dumps({'exact': True, 'source_sha256': 'x'}))
    s['inflight'] = {'function': 'f', 'profile': 'intake', 'receipt': str(receipt)}
    kwargs['state_path'].write_text(json.dumps(s))
    monkeypatch.setattr(campaign, 'execute', lambda **kw: pytest.fail('reexecuted durable result'))
    resumed = campaign.run(**kwargs, resume=True, max_work_items=1)
    assert resumed['status'] == 'cohort_objects_exact'
    assert resumed['nodes']['f']['jobs'][0]['evidence_key']
    assert not resumed['repair_queue']['work_items']
    with pytest.raises(ValueError, match='configuration'):
        campaign.run(**{**kwargs, 'scheduler': 'legacy'}, resume=True, max_work_items=0)


def test_unknown_unavailable_causes_do_not_merge():
    issues = queue.shared_issues({n: node({'status': 'unavailable'}) for n in ('a', 'b')})
    assert len(issues) == 2


def test_stale_semantics_cannot_route_new_source_to_byte_polish():
    n = node({'status': 'observed_pass', 'source_sha256': 'old'})
    assert campaign.choose(state({'f': n}))[1]['name'] == 'semantic_validate'


def test_panel_change_reopens_validation_not_elapsed_time():
    n = node({'status': 'observed_failure', 'panel_sha256': 'panel1'})
    first = queue.evidence_key(n)
    n['semantic_validation']['wall_seconds'] = 60
    assert queue.evidence_key(n) == first
    n['semantic_validation']['panel_sha256'] = 'panel2'
    assert queue.evidence_key(n) != first


def test_large_cycle_uses_one_component_not_per_node_copies():
    names = [str(i) for i in range(5000)]
    g = queue.graph({n: {names[(i + 1) % len(names)]} for i, n in enumerate(names)}, names)
    assert len(g['components']) == 1
    assert len(g['components'][0]) == 5000
    assert sum(map(len, g['callees'].values())) == 5000
