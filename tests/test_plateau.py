from solver import plateau, repair, rewrites, workspace
from pathlib import Path


def state(source, score, *, exact=False, frontend=True, labels=()):
    att = workspace.Attempt(True, score, exact, source, '', '',
        frontend={'passed': frontend}, verification={
            'kind': 'mips_object_section_certificate', 'exact': exact,
            'candidate_source_sha256': repair._digest(source)})
    return repair._State(source, att, labels)


def graph(monkeypatch, edges, scores, *, invalid=()):
    calls = []
    def tasks(states, *, pointer_context=None):
        parent = states[0]
        return [(parent, rewrites.Rewrite(child, 'layout', lambda _, c=child: c))
                for child in edges.get(parent.source, [])]
    monkeypatch.setattr(repair, '_proposal_tasks', tasks)
    def evaluate(source, parent, rw):
        calls.append((source, parent.source))
        return state(source, scores[source], exact=source == 'exact',
                     frontend=source not in invalid).attempt
    return calls, evaluate


def test_plateau_fires_and_crosses_lower_scoring_intermediates(monkeypatch):
    # Many shallow siblings used to consume the full compile budget.
    edges = {'root': ['a', 'b'] + [f'dead{i}' for i in range(20)],
             'a': ['aa'], 'aa': ['exact']}
    scores = {v: 97 for vs in edges.values() for v in vs}
    scores.update(a=80, aa=70, exact=100)
    calls, evaluate = graph(monkeypatch, edges, scores)
    monkeypatch.setattr(workspace, 'score', lambda ws, repo, name, source, **kw:
                        state(source, 99 if source == 'root' else scores[source],
                              exact=source == 'exact').attempt)
    old, _, _ = repair.search(Path('.'), 'f', 'root', Path('.'),
                             max_pairs=6, max_depth=4, verbose=False)
    assert not old.exact
    best, log = plateau.search(state('root', 99), evaluate, budget=6, max_depth=4,
        config=plateau.Config(patience=1, quantum=1, lookahead=2))
    assert plateau.verified(best)
    assert ('aa', 'a') in calls and ('exact', 'aa') in calls
    assert any('plateau detected' in line for line in log)
    assert len(calls) <= 6


def test_no_gain_keeps_original_and_deduplicates(monkeypatch):
    calls, evaluate = graph(monkeypatch, {'root': ['a', 'a'], 'a': ['root']}, {'a': 80})
    best, log = plateau.search(state('root', 99), evaluate, budget=10, max_depth=4)
    assert best.source == 'root'
    assert calls == [('a', 'root')]
    assert any('exhausted' in line for line in log)


def test_exact_without_frontend_is_not_accepted(monkeypatch):
    calls, evaluate = graph(monkeypatch, {'root': ['exact']}, {'exact': 100}, invalid={'exact'})
    best, _ = plateau.search(state('root', 99), evaluate, budget=3, max_depth=4)
    assert best.source == 'root'
    assert not plateau.verified(state('exact', 100, exact=True, frontend=False))


def test_stale_certificate_is_not_exact():
    candidate = state('candidate', 100, exact=True)
    candidate.attempt.verification['candidate_source_sha256'] = 'stale'
    assert not plateau.verified(candidate)


def test_budget_and_depth_are_hard_bounds(monkeypatch):
    calls, evaluate = graph(monkeypatch, {'root': ['a', 'b'], 'a': ['exact']},
                            {'a': 98, 'b': 97, 'exact': 100})
    best, _ = plateau.search(state('root', 99), evaluate, budget=1, max_depth=1)
    assert len(calls) == 1 and best.source == 'root'


def test_pool_can_revisit_parent_for_remaining_siblings(monkeypatch):
    calls, evaluate = graph(monkeypatch, {'root': ['a', 'b', 'exact']},
                            {'a': 90, 'b': 80, 'exact': 100})
    best, _ = plateau.search(state('root', 99), evaluate, budget=3, max_depth=2,
                             config=plateau.Config(quantum=1))
    assert plateau.verified(best) and len(calls) == 3


def test_repair_entrypoint_wires_opt_in_controller(monkeypatch):
    calls, evaluate = graph(monkeypatch, {'root': ['a'], 'a': ['exact']},
                            {'a': 80, 'exact': 100})
    def score(ws, repo, name, source, **kwargs):
        if source == 'root':
            return state(source, 99).attempt
        return evaluate(source, state('parent', 99), None)
    monkeypatch.setattr(workspace, 'score', score)
    att, source, log = repair.search(Path('.'), 'f', 'root', Path('.'),
        max_pairs=3, plateau_config=plateau.Config(patience=1, quantum=1))
    assert att.exact and source == 'exact'
    assert any('plateau detected' in line for line in log)
