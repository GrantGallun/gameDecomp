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


def revalidated(n):
    """The node's stored semantic verdict is current for this environment code (revalidate@ already spent)."""
    n['jobs'].append({'profile': 'revalidate@' + queue.semantic_environment_digest(),
                      'source_sha256': n['source_sha256']})
    return n


def state(nodes, **config):
    return {'config': {'scheduler': 'evidence-v1', 'model_calls': 2, **config}, 'nodes': nodes}


@pytest.mark.parametrize('semantic,expected', [
    (None, 'semantic_validate'),
    ({'status': 'observed_failure', 'counts': {'failed': 1}}, 'semantic_counterexample'),
    ({'status': 'observed_pass_with_execution_debt'}, 'local_rewrites'),
    ({'status': 'unavailable', 'reason': 'hardware'}, 'local_rewrites'),
])
def test_route_by_evidence(semantic, expected):
    n = revalidated(node(semantic))
    assert campaign.choose(state({'f': n}))[1]['name'] == expected


def test_inconclusive_abi_comparisons_do_not_spend_byte_polish_model_budget():
    n = revalidated(node({'status': 'inconclusive', 'counts': {'passed': 8, 'inconclusive': 56}}))
    key = queue.evidence_key(n)
    assert queue.lane(n) == queue.Lane.ENVIRONMENT
    # Reproduce the old key explicitly: scheduling is not new evidence.
    from solver.evidence_schedule import fingerprint
    assert key == fingerprint({
        'source': 'source', 'lane': 'byte',
        'compiler': {'compiled': True, 'compiler_error_signature': None},
        'frontend': {'passed': True, 'status': None, 'diagnostics': None},
        'semantic': {k: n['semantic_validation'].get(k) for k in (
            'status', 'source_sha256', 'panel_sha256', 'semantic_key', 'counts', 'reason', 'feedback')},
        'blocker': None})
    # A persisted byte-lane deterministic attempt remains spent after migration.
    n['jobs'].append({'profile': 'local_rewrites', 'evidence_key': key, 'lane': 'byte'})
    s = state({'f': n})
    _, p = campaign.choose(s)
    assert p['name'] == 'deeper_composition' and p['model'] is False
    campaign.accept(n, p, {'semantic_validation': n['semantic_validation']}, Path('r.json'))
    assert campaign.choose(s) is None
    # Measured disagreement still opens causal repair when new evidence arrives.
    n['semantic_validation'] = {'status': 'observed_failure', 'counts': {'failed': 1}}
    assert campaign.choose(s)[1]['name'] == 'semantic_counterexample'


def test_inconclusive_shared_obstructions_ignore_frequency_but_preserve_evidence():
    def receipt(error, count, case):
        return {'status': 'inconclusive', 'outcome_accounting': {
            'inconclusive_reason_groups': [{'comparison_status': 'inconclusive',
                'reasons': [error], 'target_status': 'returned', 'candidate_status': 'unsupported',
                'target_error': '', 'candidate_error': error, 'count': count, 'example_case': case}]}}
    a = node(receipt('unknown call arity for __ll_lshift', 64, 'boundary-0'))
    b = node(receipt('unknown call arity for __ll_lshift', 56, 'boundary-9'))
    c = node(receipt('unknown call arity for other', 64, 'boundary-0'))
    issues = queue.shared_issues({'a': a, 'b': b, 'c': c})
    assert sorted(i['affected_functions'] for i in issues.values()) == [('a', 'b'), ('c',)]
    assert all(not i['executable'] for i in issues.values())
    b['semantic_validation']['outcome_accounting']['omitted_reason_groups'] = 1
    assert len(queue.shared_issues({'a': a, 'b': b})) == 2
    assert len(queue.shared_issues({n: node({'status': 'inconclusive'}) for n in ('a', 'b')})) == 2


def test_retry_requires_new_measured_evidence_not_logging_changes():
    n = revalidated(node({'status': 'observed_failure', 'counts': {'failed': 1}}))
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


def locality_state(**config):
    """Two units of equal-cost work: 'near' has one leftover, 'wide' has three."""
    nodes = {name: node({'status': 'observed_pass'}) for name in
             ('near1', 'wide1', 'wide2', 'wide3')}
    s = state(nodes, **config)
    s['tu_index'] = {'near1': 'near.o', 'wide1': 'wide.o', 'wide2': 'wide.o', 'wide3': 'wide.o'}
    return s


def test_locality_fires_on_the_near_finished_unit():
    # Motivating residual: 79 units hold exactly one unfinished function while
    # the queue round-robins every file at once. Name order alone would pick
    # 'near1' here, so the sizes make the old order prefer the wide unit.
    s = locality_state()
    s['nodes']['wide1']['instruction_count'] = 1
    assert campaign.choose(s)[0] == 'near1'
    snapshot = queue.project(s, campaign.PROFILES)[0]
    assert snapshot['locality'] == {'indexed': True, 'units_with_work': 2, 'unindexed_functions': []}
    assert snapshot['work_items']['near1']['priority'][4] == 1
    assert snapshot['work_items']['wide1']['priority'][4] == 3


def test_absent_index_reproduces_previous_order_and_unknown_unit_sorts_last():
    s = locality_state()
    s['nodes']['wide1']['instruction_count'] = 1
    without = copy.deepcopy(s)
    del without['tu_index']
    assert campaign.choose(without)[0] == 'wide1'
    assert queue.project(without, campaign.PROFILES)[0]['locality'] == {
        'indexed': False, 'units_with_work': 0, 'unindexed_functions': []}
    # A function missing from a present index is reported, not quietly favored.
    del s['tu_index']['near1']
    assert campaign.choose(s)[0] == 'wide1'
    assert queue.project(s, campaign.PROFILES)[0]['locality']['unindexed_functions'] == ['near1']


def test_locality_orders_within_bands_and_never_starves_or_reserves():
    s = locality_state()
    s['nodes']['near1']['jobs'] = [{'profile': 'a'}, {'profile': 'b'}]
    # A visited near-unit function drops a band: fairness still outranks locality.
    assert campaign.choose(s)[0] == 'wide1'
    # Parked-only leftovers do not make a finished unit look cheapest forever.
    s = locality_state()
    s['nodes']['wide2']['status'] = s['nodes']['wide3']['status'] = 'parked'
    assert queue.project(s, campaign.PROFILES)[0]['work_items']['wide1']['priority'][4] == 1


def test_equal_sized_clusters_stay_together_in_binary_order():
    s = state({n: node({'status': 'observed_pass'}, instruction_count=size, address=addr)
               for n, size, addr in [('a', 1, 0x2000), ('b', 9, 0x2010),
                                      ('c', 2, 0x1000), ('d', 8, 0x1010)]})
    s['tu_index'] = {'a': 'first-alphabetically', 'b': 'first-alphabetically',
                     'c': 'last-alphabetically', 'd': 'last-alphabetically'}
    projected, _ = queue.project(s, campaign.PROFILES)
    ordered = sorted(projected['work_items'].values(), key=lambda r: r['priority'])
    assert [r['function'] for r in ordered] == ['c', 'd', 'a', 'b']


def test_callee_first_beats_high_leverage_caller_but_not_fairness():
    names = ['leaf', 'caller', 'root1', 'root2', 'root3']
    s = state({n: node({'status': 'observed_pass'}) for n in names})
    s['tu_index'] = {n: n for n in names}
    s['dependency_graph'] = queue.graph({'caller': ['leaf'],
        **{n: ['caller'] for n in names if n.startswith('root')}}, names)
    projected, selected = queue.project(s, campaign.PROFILES)
    assert selected[0] == 'leaf'
    assert projected['work_items']['caller']['dependency_depth'] == 1
    assert projected['work_items']['root1']['dependency_depth'] == 2
    # Retrying a leaf cannot starve fresh callers indefinitely.
    s['nodes']['leaf']['jobs'] = [{'profile': 'other'}] * 2
    assert campaign.choose(s)[0] == 'caller'
    s['nodes']['leaf']['status'] = 'object_exact'
    assert queue.project(s, campaign.PROFILES)[0]['work_items']['caller']['dependency_depth'] == 0


def test_cycles_remain_eligible_and_exhausted_work_does_not_inflate_units():
    s = locality_state()
    for name in ('wide2', 'wide3'):
        n = s['nodes'][name]
        n['jobs'] = [{'profile': p['name'], 'evidence_key': queue.evidence_key(n)}
                     for p in campaign.PROFILES]
    s['dependency_graph'] = queue.graph({'wide1': ['near1'], 'near1': ['wide1']}, s['nodes'])
    projected, _ = queue.project(s, campaign.PROFILES)
    assert set(projected['work_items']) == {'wide1', 'near1'}
    assert {r['dependency_depth'] for r in projected['work_items'].values()} == {0}
    assert projected['work_items']['wide1']['priority'][4] == 1


def test_dependency_depth_handles_deep_graph_without_recursion():
    names = [str(i) for i in range(5000)]
    deps = {names[i]: [names[i-1]] for i in range(1, len(names))}
    assert queue.dependency_depths(names, deps)['4999'] == 4999
    deps['0'] = ['4999']
    assert set(queue.dependency_depths(names, deps).values()) == {0}


def test_partial_index_and_json_preserve_ordering_and_evidence_keys():
    s = locality_state()
    s['tu_index']['near1'] = None
    before = copy.deepcopy(s)
    projected, selected = queue.project(s, campaign.PROFILES)
    assert s == before
    assert projected['locality']['unindexed_functions'] == ['near1']
    assert queue.project(json.loads(json.dumps(s)), campaign.PROFILES) == (projected, selected)
    assert selected[1]['evidence_key'] == queue.evidence_key(s['nodes'][selected[0]])


def test_campaign_indexes_units_from_layout_not_the_reference_map(tmp_path):
    db = tmp_path / 'kb.sqlite'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE functions(name,addr,size,insn_count,tu_id)')
        c.execute('CREATE TABLE tus(id,name)')
        c.execute("INSERT INTO tus VALUES(1,'build/src/menu/menu.o')")
        # 'g' follows 'f' with no padding (same unit); 'h' follows a 16-byte pad.
        c.execute("INSERT INTO functions VALUES('f',4096,16,4,1),('g',4112,16,4,1),('h',4144,16,4,2)")
        assert campaign.translation_units(c) == {
            'f': 'unit@0x00001000', 'g': 'unit@0x00001000', 'h': 'unit@0x00001030'}
        # The linker-map assignment stays available as an oracle only.
        assert campaign.reference_units(c) == {
            'f': 'build/src/menu/menu.o', 'g': 'build/src/menu/menu.o', 'h': 'tu:2'}
    with sqlite3.connect(tmp_path / 'old.sqlite') as c:
        c.execute('CREATE TABLE legacy(name)')
        assert campaign.translation_units(c) == {}


def test_layout_clustering_declines_incomplete_rows_rather_than_guessing():
    from miner import units
    assert units.clusters([]) == {}
    assert units.clusters([(4096, None, 'f'), (None, 16, 'g')]) == {}
    # One unlocatable function must not drag located ones into a shared bucket.
    assert units.clusters([(4096, 16, 'f'), (None, 16, 'g')]) == {'f': 'unit@0x00001000'}


KB = Path('eval/results/resume-pipeline-20260908/campaign.sqlite')


@pytest.mark.skipif(not KB.exists(), reason='campaign KB not present')
def test_layout_clustering_checked_against_the_reference_linker_map():
    """The finished decomp's map is the oracle: it checks, it never feeds.

    Measured 2026-09-11 on SBK1: 170/211 boundaries recovered, 4 false splits.
    A miss merges two ADJACENT units; a false split is the damaging error, so
    it is bounded tightly. A large drop here is a regression to explain.
    """
    with sqlite3.connect(f'file:{KB.as_posix()}?mode=ro', uri=True) as conn:
        rows = [r for r in conn.execute('SELECT addr,size,name FROM functions') if r[0] is not None]
        reference = campaign.reference_units(conn)
    ordered = sorted(rows)
    truth = {a['name'] for a, b in zip(
        [{'name': n} for _, _, n in ordered], [{'name': n} for _, _, n in ordered][1:])
        if reference.get(a['name']) != reference.get(b['name'])}
    from miner import units
    predicted = units.boundaries(ordered)
    hit = len(truth & predicted)
    precision = hit / max(1, len(predicted))
    recall = hit / max(1, len(truth))
    assert precision >= 0.95, f'false splits: {sorted(predicted - truth)}'
    assert recall >= 0.75, f'recovered {hit} of {len(truth)}'


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
