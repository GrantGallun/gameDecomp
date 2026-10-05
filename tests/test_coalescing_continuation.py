import importlib.util
from pathlib import Path


def module():
    path = Path(__file__).parents[1] / 'eval/results/coalescing-continuation-20260928/run.py'
    spec = importlib.util.spec_from_file_location('coalescing_continuation', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_selects_actual_gradient_and_score_improvements_without_losing_original():
    root = dict(source='root', gradient=[8, 2, 3], score=90, compiled=True, exact=False)
    rows = [dict(root, source='gradient', gradient=[6, 3, 4], score=89),
            dict(root, source='score', gradient=[9, 1, 1], score=95),
            dict(root, source='failed', gradient=[0, 0, 0], score=100, compiled=False),
            dict(root, source='noop', same_object=True, new_moves=7)]
    chosen = module().choose_roots(root, rows, noop=True)
    assert [(r['role'], r['source']) for r in chosen] == [
        ('original', 'root'), ('gradient', 'gradient'), ('score', 'score'), ('noop', 'noop')]


def test_deduplicates_shared_winner_and_declines_unsupported_noop():
    root = dict(source='root', gradient=[8, 2, 3], score=90, compiled=True, exact=False)
    rows = [dict(root, source='both', gradient=[6, 1, 1], score=95),
            dict(root, source='noop', same_object=True, new_moves=0)]
    assert [(r['role'], r['source']) for r in module().choose_roots(root, rows, noop=True)] == [
        ('original', 'root'), ('gradient', 'both')]


def test_known_exact_or_no_improvement_does_not_enter_continuation_cohort():
    root = dict(source='root', gradient=[8, 2, 3], score=90, compiled=True, exact=False)
    assert module().choose_roots(root, [dict(root, source='worse', score=80)]) == []
    assert module().choose_roots(root, [dict(root, source='exact', exact=True, score=100)]) == []
