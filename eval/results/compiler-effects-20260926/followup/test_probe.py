import importlib.util
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location('compiler_effects_followup',
                                             Path(__file__).with_name('probe.py'))
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def child(ordinal, gradient, score, *, compiled=True, frontend=True):
    return {'ordinal': ordinal, 'gradient': gradient, 'score': score,
            'compiled': compiled, 'frontend_passed': frontend}


def test_selects_best_valid_improvement_with_stable_tiebreak():
    baseline = {'gradient': [2, 3, 4]}
    children = [child(0, [1, 2, 0], 99.), child(1, [1, 1, 8], 92.),
                child(2, [1, 1, 8], 93.), child(3, [1, 1, 8], 93.),
                child(4, [0, 0, 0], 100., frontend=False),
                child(5, [0, 0, 0], 100., compiled=False)]
    assert probe.best_improving_child(baseline, children)['ordinal'] == 2


def test_declines_when_only_nonimprovements_exist():
    assert probe.best_improving_child({'gradient': [1, 2, 3]},
                                      [child(0, [1, 2, 3], 99.),
                                       child(1, [2, 0, 0], 99.)]) is None


def test_score_child_logs_selected_real_parent(monkeypatch, tmp_path):
    captured = {}

    def fake_score(ws, repo, tag, source, **kwargs):
        captured.update(kwargs)
        return probe.workspace.Attempt(False, 0., False, '', 'compile failure', '', receipt_id=27)

    monkeypatch.setattr(probe.workspace, 'score', fake_score)
    root = {'parent_receipt_id': 17, 'parent_sha256': 'parent-hash',
            'followup_manifest_sha256': 'manifest-hash'}
    proposal = {'ordinal': 4, 'family': 'stmt_move', 'label': 'stmt_move:1->2',
                'source_sha256': 'proposal-hash'}
    result = probe.score_child(object(), tmp_path, tmp_path, 'example', root, proposal, 'void example() {}')
    assert captured['parent_attempt_id'] == 17
    assert captured['relation'] == 'prospective-second-edit'
    assert captured['extra']['training_eligible'] is False
    assert captured['extra']['header_assisted'] is True
    assert result['receipt_id'] == 27
