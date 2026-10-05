"""Search composition, budget, and incumbent protection at the compiler boundary."""
from collections import Counter


def front(errors):
    return {'status': 'passed' if errors == 0 else 'rejected', 'error_count': errors,
            'errors': [{'line': n + 1, 'column': 1, 'what': 'undeclared identifier'}
                       for n in range(errors)], 'errors_truncated': False}


def verdict(compiled=False, exact=False, score=0):
    return dict(compiled=compiled, exact=exact, score=score, stderr='', diff='')


def test_revisits_an_earlier_owner_after_a_partial_repair_and_scores_each_child():
    from eval.intake_search import search
    scored = []
    def compile_source(source, parent, action):
        scored.append((source, parent, action))
        return verdict(source == 'done', source == 'done', 100 if source == 'done' else 0)
    def owner(ctx, params):
        child = {'draft': 'partial', 'partial': 'done'}.get(ctx['candidate'])
        # The first owner needs the cast produced later in the sequence.
        return {'changed': ctx['candidate'] == 'partial', 'source': child, 'reason': 'needs cast'}
    def cast(ctx, params):
        assert params['allow_partial']
        return {'changed': ctx['candidate'] == 'draft', 'source': 'partial', 'reason': 'no cast'}
    result = search(dict(candidate='draft', initial_verdict=verdict()),
                    runners={'owner': owner, 'cast': cast}, sequence=('owner', 'cast'),
                    score=compile_source, observe=lambda s: front({'draft': 2, 'partial': 1, 'done': 0}[s]),
                    max_rounds=3, max_attempts=4)
    assert result['source'] == 'done' and result['verdict']['exact']
    assert [s for s, _, _ in scored] == ['partial', 'done']
    assert result['attempts'] == 2
    assert any(t['reason'] == 'needs cast' for t in result['trace'])
    assert result['nodes'][2]['parent'] == result['nodes'][1]['source_sha256']


def test_explores_a_lower_scoring_alternative_without_losing_the_incumbent():
    from eval.intake_search import search
    def runner(ctx, params):
        return {'changed': ctx['candidate'] == 'best', 'source': 'branch'}
    result = search(dict(candidate='best', initial_verdict=verdict(True, False, 90)),
                    runners={'repair': runner}, sequence=('repair',),
                    score=lambda *args: verdict(False),
                    observe=lambda s: front(2 if s == 'best' else 1), max_rounds=3)
    assert result['source'] == 'best'
    assert any(n['source'] == 'branch' for n in result['nodes'])
    assert result['stop_reason'] == 'fixed-point'


def test_budget_and_source_deduplication_bound_a_cycle():
    from eval.intake_search import search
    calls = Counter()
    def cycle(ctx, params):
        return {'changed': True, 'source': 'b' if ctx['candidate'] == 'a' else 'a'}
    def score(source, *_):
        calls[source] += 1
        return verdict()
    result = search(dict(candidate='a', initial_verdict=verdict()),
                    runners={'cycle': cycle}, sequence=('cycle',), score=score,
                    observe=lambda s: front(1), max_rounds=10, max_attempts=2)
    assert calls == {'b': 1}
    assert result['stop_reason'] == 'fixed-point'
    result = search(dict(candidate='a', initial_verdict=verdict()),
                    runners={'grow': lambda c, p: {'changed': True, 'source': c['candidate'] + 'x'}},
                    sequence=('grow',), score=score, observe=lambda s: front(1),
                    max_rounds=10, max_attempts=2)
    assert result['attempts'] == 2 and result['stop_reason'] == 'attempt-budget'


def test_exact_incumbent_is_never_expanded():
    from eval.intake_search import search
    def forbidden(*args):
        raise AssertionError('an exact incumbent must stop search')
    result = search(dict(candidate='exact', initial_verdict=verdict(True, True, 100)),
                    runners={'repair': forbidden}, sequence=('repair',), score=forbidden,
                    observe=lambda s: front(0))
    assert result['source'] == 'exact' and result['attempts'] == 0


def test_incomplete_or_failed_zero_error_candidate_cannot_replace_the_incumbent():
    from eval.intake_search import search
    for bad in ({'status': 'rejected', 'error_count': 0, 'errors': []},
                {**front(1), 'errors_truncated': True}):
        result = search(dict(candidate='good', initial_verdict=verdict()),
            runners={'repair': lambda c, p: {'changed': True, 'source': 'bad'}}, sequence=('repair',),
            score=lambda *a: verdict(), observe=lambda s: front(2) if s == 'good' else bad)
        assert result['source'] == 'good'


def test_probe_routes_the_opted_in_sequence_through_composition(tmp_path, monkeypatch):
    import json
    from eval import intake_probe, intake_runners, tool_agent_run
    from eval.tool_agent import Context
    from solver import frontend_diagnostics
    source = 'void f(void) {}'
    child = source + '\n/* compiled */'
    context = Context(function='f', candidate=source, repo=str(tmp_path), target='build/src/f.o',
                      initial_verdict=verdict(), compile_fn=lambda s, **kw: verdict(True, False, 90))
    monkeypatch.setattr(tool_agent_run, 'build_context', lambda *a, **kw: (context, None))
    monkeypatch.setattr(intake_probe, 'candidates', lambda *a, **kw: ['f'])
    monkeypatch.setattr(intake_probe, '_placeholder_widths', lambda *a: ({}, {}))
    monkeypatch.setattr(intake_probe, '_diagnostic_chain', lambda *a: {'clang': 'passed', 'errors': 0, 'classes': []})
    monkeypatch.setattr(frontend_diagnostics, 'analyse', lambda s, **kw: front(0 if s == child else 1))
    monkeypatch.setattr(intake_probe, 'SEQUENCE', ('repair',))
    monkeypatch.setitem(intake_runners.RUNNERS, 'repair', lambda c, p:
                        {'changed': c['candidate'] != child, 'source': child})
    path = tmp_path / 'receipt.json'
    assert intake_probe.main(['--kb', str(tmp_path / 'kb.sqlite'), '--repo', str(tmp_path),
        '--split', str(tmp_path / 'no-split.json'), '--out', str(path), '--want', '1',
        '--repair-rounds', '3', '--repair-budget', '2']) == 0
    row = json.loads(path.read_text())['rows'][0]
    assert row['sequence']['compiled']
    assert row['repair_search']['attempts'] == 1
    assert row['repair_search']['stop_reason'] == 'fixed-point'


def test_probe_persists_parent_edges_for_composed_compiler_attempts(tmp_path, monkeypatch):
    import sqlite3
    from pathlib import Path
    from eval import intake_probe, intake_runners
    from solver import workspace, frontend_diagnostics
    db = tmp_path / 'attempts.sqlite'
    with sqlite3.connect(db) as conn:
        conn.executescript((Path(__file__).resolve().parents[1] / 'kb/schema.sql').read_text())
        conn.execute("INSERT INTO functions(addr,name) VALUES(1,'f')")
    source = 'void f(void) {}'
    monkeypatch.setattr(workspace, 'bootstrap', lambda *a: tmp_path)
    monkeypatch.setattr(workspace, 'm2c_draft', lambda *a: source)
    def logged_score(ws, repo, name, code, conn=None, func=None, **metadata):
        result = workspace.Attempt(code != source, 0, False, '', '', '',
                                   compiler_recipe={'target': 'build/src/f.o'})
        workspace.record_attempt(conn, func, code, result, **metadata)
        return result
    monkeypatch.setattr(workspace, 'score', logged_score)
    monkeypatch.setattr(intake_probe, 'candidates', lambda *a, **kw: ['f'])
    monkeypatch.setattr(intake_probe, '_placeholder_widths', lambda *a: ({}, {}))
    monkeypatch.setattr(intake_probe, '_diagnostic_chain', lambda *a: {'clang': 'passed', 'errors': 0, 'classes': []})
    monkeypatch.setattr(frontend_diagnostics, 'analyse', lambda s, **kw: front(1 if s == source else 0))
    monkeypatch.setattr(intake_probe, 'SEQUENCE', ('repair',))
    monkeypatch.setitem(intake_runners.RUNNERS, 'repair', lambda c, p:
                        {'changed': c['candidate'] == source, 'source': source + '\n/* child */'})
    assert intake_probe.main(['--kb', str(db), '--repo', str(tmp_path), '--split', str(tmp_path / 'none'),
        '--out', str(tmp_path / 'result.json'), '--want', '1', '--repair-rounds', '3']) == 0
    with sqlite3.connect(db) as conn:
        edges = conn.execute('SELECT parent_attempt_id,child_attempt_id,action FROM attempt_edges').fetchall()
        assert len(edges) == 1 and edges[0][0] == 1 and edges[0][2] == 'repair'
        parent = conn.execute('SELECT source_code FROM attempts WHERE id=?', (edges[0][0],)).fetchone()[0]
        child = conn.execute('SELECT source_code FROM attempts WHERE id=?', (edges[0][1],)).fetchone()[0]
        assert parent == source and child == source + '\n/* child */'
