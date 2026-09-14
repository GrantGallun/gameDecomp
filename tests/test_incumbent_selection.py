"""Exploration order must not silently replace an equally good incumbent."""

import pytest

from solver import modelrepair, workspace


def attempt(score=90, *, compiled=True, frontend=True):
    return workspace.Attempt(compiled, score, False, '', '', '',
                             frontend={'passed': frontend})


def search(tmp_path, root, candidate, root_attempt, child_attempt, *, keys=(1, 1)):
    retained = modelrepair.CandidateState(candidate, child_attempt)
    result = modelrepair.search(
        tmp_path, 'f', root, tmp_path, model='unused', endpoint='unused',
        base_attempt=root_attempt, base_object_path=tmp_path/'root.o',
        initial_states=(retained,), beam_width=1, max_calls=0,
        semantic_evaluator=lambda state: {
            'panel_sha256': 'same-fixed-panel',
            'semantic_key': [keys[0] if state.source == root else keys[1]],
        })
    return result, retained


def test_equal_quality_retained_source_stays_explorable_without_replacing_root(tmp_path):
    root = 'int f(void) { return 1; }'
    candidate = '#define UNUSED 1\n' + root
    root_attempt, child_attempt = attempt(), attempt()
    result, retained = search(tmp_path, root, candidate, root_attempt, child_attempt)
    # A width-one semantic frontier deliberately keeps the alternative. It is
    # an exploration choice, not evidence that the selected source improved.
    assert result.frontier == [retained]
    assert result.best_source == root
    assert result.best_attempt is root_attempt
    assert result.best_object_path == tmp_path/'root.o'
    assert result.best_semantic.source == root
    assert result.best_byte.source == root


@pytest.mark.parametrize('root_score,child_score,keys,compiled,frontend', [
    (90, 90, (1, 2), True, True),   # behavioral improvement at equal bytes
    (90, 80, (1, 2), True, True),   # behavior takes precedence over similarity
    (90, 91, (1, 1), True, True),   # byte improvement
    (0, 20, (1, 1), False, False),  # recovery from compile failure
    (90, 90, (1, 1), True, False),  # frontend gate repair at equal bytes
])
def test_measured_initial_improvements_still_replace_root(
        tmp_path, root_score, child_score, keys, compiled, frontend):
    root = 'int f(void) { return 1; }'
    candidate = 'int f(void) { return 2; }'
    result, _ = search(tmp_path, root, candidate,
        attempt(root_score, compiled=compiled, frontend=frontend),
        attempt(child_score), keys=keys)
    assert result.best_source == candidate


def test_selection_checks_all_initial_states_even_when_exploration_omits_best(monkeypatch, tmp_path):
    root = 'int f(void) { return 1; }'
    child = modelrepair.CandidateState('int f(void) { return 2; }', attempt(95))
    # Structural diversity is allowed to choose a different exploration parent.
    monkeypatch.setattr(modelrepair, '_frontier', lambda states, width: [states[0]])
    result = modelrepair.search(tmp_path, 'f', root, tmp_path,
        model='unused', endpoint='unused', base_attempt=attempt(),
        initial_states=(child,), max_calls=0)
    assert result.best_source == child.source
    assert result.best_attempt is child.attempt
