"""Small stage and replay checks; the compiler itself is the outcome oracle."""
import importlib.util
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location('compiler_effects_benchmark',
                                             Path(__file__).with_name('benchmark.py'))
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


def test_split_keeps_translation_units_together():
    roots = [{'function': f'f{i}', 'tu': f'tu{i // 2}'} for i in range(40)]
    benchmark.choose_split(roots)
    assert sum(r['split'] == 'development' for r in roots) == 16
    assert all(len({r['split'] for r in roots if r['tu'] == tu}) == 1
               for tu in {r['tu'] for r in roots})


@pytest.mark.parametrize('tu_count,limit', [(50, 40), (3, 10), (20, 16)])
def test_lazy_cohort_equals_exhaustive_selection(tu_count, limit):
    metadata = [{'function': f'f{i:03}', 'tu': f'tu{i % tu_count}'} for i in range(150)]

    def proposals(root):
        index = int(root['function'][1:])
        return [{'ordinal': 0}] if index % 7 else []

    ordered = sorted(({**r, 'proposals': proposals(r)} for r in metadata),
                     key=lambda r: (benchmark.digest(benchmark.SEED + r['function']), r['function']))
    eligible = [r for r in ordered if r['proposals']]
    exhaustive, remainder, per_tu = [], [], {}
    for root in eligible:
        if per_tu.get(root['tu'], 0) < 2 and len(exhaustive) < limit:
            exhaustive.append(root)
            per_tu[root['tu']] = per_tu.get(root['tu'], 0) + 1
        else:
            remainder.append(root)
    exhaustive += remainder[:limit - len(exhaustive)]
    selected, checked = benchmark.select_roots(metadata, proposals, limit)
    assert [r['function'] for r in selected] == [r['function'] for r in exhaustive]
    assert checked <= len(metadata)


def test_rank_order_must_be_permutation():
    proposals = [{'ordinal': 0}, {'ordinal': 1}]
    assert benchmark.ordered_ordinals([{'ordinal': 1}, {'ordinal': 0}], proposals) == [1, 0]
    with pytest.raises(ValueError):
        benchmark.ordered_ordinals([{'ordinal': 0}, {'ordinal': 0}], proposals)


def test_replay_counts_first_exact_and_gradient():
    baseline = {'score': 90., 'gradient': [1, 2, 3]}
    children = [
        {'ordinal': 0, 'compiled': True, 'frontend_passed': True, 'score': 80.,
         'exact': False, 'certificate_exact': False, 'gradient': [1, 3, 3],
         'elapsed_seconds': 3.},
        {'ordinal': 1, 'compiled': True, 'frontend_passed': True, 'score': 95.,
         'exact': False, 'certificate_exact': False, 'gradient': [1, 1, 3],
         'elapsed_seconds': 2.},
        {'ordinal': 2, 'compiled': True, 'frontend_passed': True, 'score': 100.,
         'exact': True, 'certificate_exact': True, 'gradient': [0, 0, 0],
         'elapsed_seconds': 1.},
    ]
    original = benchmark.replay(children, baseline, [0, 1, 2])
    ranked = benchmark.replay(children, baseline, [2, 1, 0])
    assert original['exact']['first_call'] == 3
    assert ranked['exact']['first_call'] == 1
    assert original['fault_gradient']['first_call'] == 2
    assert original['exact']['top4_replay_calls'] == 3
    assert original['exact']['first_success_replay_compile_seconds'] == 6.
    assert ranked['exact']['first_success_replay_compile_seconds'] == 1.


def test_code_pins_reject_drift(tmp_path, monkeypatch):
    manifest = {'kind': 'fixture'}
    benchmark.seal(tmp_path / 'manifest.json', manifest)
    current = {'predictor.py': 'sha-a'}
    monkeypatch.setattr(benchmark, 'code_inputs', lambda _manifest: dict(current))
    benchmark.pin_code(tmp_path, manifest)
    benchmark.check_code_pins(tmp_path, manifest)
    current['predictor.py'] = 'sha-b'
    with pytest.raises(ValueError, match='changed after pin'):
        benchmark.check_code_pins(tmp_path, manifest)


def test_state_distance_uses_missing_keys():
    before = {'opcode:lw': 1.}
    actual = {'opcode:lw': 1., 'opcode:sw': 1.}
    children = [{'ordinal': 0, 'after': actual}]
    ranked = [{'ordinal': 0, 'forecast': {'predicted_state': actual, 'status': 'predicted'}}]
    result = benchmark.state_prediction_error(before, children, ranked)
    assert result['predicted_mean_distance'] == 0.
    assert result['no_change_mean_distance'] > 0.
