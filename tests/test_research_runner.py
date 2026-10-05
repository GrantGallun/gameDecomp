"""Allocation tests use an explicit fake oracle; native smoke tests certify objects."""
import importlib

import pytest

from solver.regalloc_search import Compiled


def runner():
    try:
        return importlib.import_module('eval.research_suite.runner')
    except ModuleNotFoundError:
        pytest.fail('runner is not implemented')


class Compiler:
    target_dump = 'f:\n  addiu v0,a0,1\n  jr ra\n  nop\n'

    def __init__(self):
        self.calls = []
        self.key_calls = 0

    def key(self, source):
        self.key_calls += 1
        return source

    def same_object(self, prior, actual):
        return None

    def __call__(self, source, label, parent_source=None):
        self.calls.append(source)
        return Compiled(True, source == 'winner', self.target_dump.replace(',1', ',2'))


@pytest.mark.parametrize('arm', ['beam', 'archive', 'explore', 'archive_explore',
                                'production', 'production_diverse', 'mutation_count', 'evolvability',
                                'mutation_count_diverse', 'evolvability_diverse', 'pure', 'compose', 'types',
                                'production_coalesce', 'evolvability_coalesce',
                                'staged', 'interleaved', 'brackets'])
def test_all_arms_count_real_callback_work_and_respect_budget(arm):
    c = Compiler()
    result = runner().run_arm('f', 'root', c, arm=arm, budget=3, seed=4,
                             proposals=[{'source_text': 'loser', 'label': 'recorded'}])
    assert 0 < result['compiles'] <= 3
    assert result['compiles'] == len(c.calls)
    assert c.calls[0] == 'root'
    if arm in {'production', 'production_diverse', 'mutation_count', 'evolvability',
               'mutation_count_diverse', 'evolvability_diverse',
               'production_coalesce', 'evolvability_coalesce'}:
        assert result['key_calls'] == c.key_calls > 0
        assert result['policy']['enable'] is True
        assert result['budget_spent'] >= len(c.calls)


@pytest.mark.parametrize('arm', ['mutation_count', 'evolvability',
                               'mutation_count_diverse', 'evolvability_diverse'])
def test_mutation_arms_use_production_engine_and_expose_policy_receipts(monkeypatch, arm):
    from solver import regalloc_mutations
    monkeypatch.setattr(regalloc_mutations, 'variants', lambda source, *args, **kwargs:
                        [('bridge', 'shape', 'bridge')] if source == 'root' else
                        [('winner', 'repair', 'winner')] if source == 'bridge' else [])
    monkeypatch.setattr(regalloc_mutations, 'enabling_variants', lambda *args: [])
    c = Compiler()
    result = runner().run_arm('f', 'root', c, arm=arm, budget=10, seed=7,
                             options={'mutation_preview': 8, 'mutation_probes': 1, 'explore_rate': 0})
    assert result['exact'] and result['best_source'] == 'winner'
    assert result['compiles'] == len(c.calls) == 3
    assert result['policy']['engine'] == 'solver.regalloc_search'
    assert result['policy']['enable'] and result['policy']['keyed']
    assert result['policy']['diverse'] == arm.endswith('_diverse')
    assert result['policy']['selection'] == arm.removesuffix('_diverse')
    assert result['policy']['mutation_preview'] == 8
    assert result['policy']['mutation_probes'] == (1 if arm.startswith('evolvability') else 0)
    assert result['policy']['selection_seed'] == 7
    assert result['budget_spent'] == pytest.approx(3.42)


@pytest.mark.parametrize('arm', ['staged', 'interleaved'])
def test_recorded_schedules_reach_same_frozen_candidate(arm):
    c = Compiler()
    result = runner().run_arm('f', 'root', c, arm=arm, budget=6, seed=0,
                             proposals=[{'source_text': 'winner', 'label': 'model_1',
                                         'generation_seconds': 2.5}])
    assert result['exact'] and result['best_source'] == 'winner'
    assert result['recorded_generation_seconds'] == 2.5
    assert result['compiles'] == len(c.calls) <= 6


def test_paired_summary_refuses_duplicate_or_different_budget_and_separates_assistance():
    r = runner()
    base = {'task': 'f', 'cluster': 'file1', 'function': 'f', 'assistance': 'synthetic',
            'arm': 'beam', 'seed': 0, 'exact': False, 'compiles': 4, 'budget': 4,
            'bundle_sha256': 'same', 'environment_sha256': 'env', 'valid': True}
    other = {**base, 'arm': 'archive', 'exact': True}
    summary = r.summarize([base, other], baseline='beam')
    assert summary['comparisons'][0]['wins'] == 1
    assert summary['comparisons'][0]['pairs'] == 1
    assert summary['comparisons'][0]['assistance'] == 'synthetic'
    assert summary['comparisons'][0]['additional_exact_functions'] == 1
    with pytest.raises(ValueError, match='duplicate'):
        r.summarize([base, base])
    with pytest.raises(ValueError, match='budget'):
        r.summarize([base, {**other, 'budget': 8}])
    assert r.summarize([base, {**other, 'valid': False}], baseline='beam')['comparisons'] == []


def test_default_summary_uses_enabled_keyed_production_baseline():
    base = {'task': 'f', 'cluster': 'file1', 'function': 'f', 'assistance': 'synthetic',
            'arm': 'production', 'seed': 0, 'exact': False, 'compiles': 3, 'budget': 4,
            'bundle_sha256': 'same', 'environment_sha256': 'env', 'valid': True,
            'comparison_scope': 'shared-production-engine'}
    other = {**base, 'arm': 'production_diverse', 'exact': True}
    comparison = runner().summarize([base, other])['comparisons'][0]
    assert comparison['baseline'] == 'production' and comparison['wins'] == 1
    assert comparison['shared_production_engine'] is True


def test_gradient_secondary_uses_complete_real_compile_observations():
    progress = runner().compiled_progress([
        {'compiled': True, 'gradient': [0, 3, 8]},
        {'compiled': False, 'gradient': [0, 0, 0]},
        {'compiled': True, 'gradient': [0, 2, 9]},
    ])
    assert progress == {'baseline_gradient': [0, 3, 8], 'best_compiled_gradient': [0, 2, 9],
                        'gradient_improved': True}
    assert runner().compiled_progress([{'compiled': False}])['gradient_improved'] is None


def test_paired_gradient_secondary_tracks_better_tie_worse_and_missing():
    base = {'task': 'f', 'cluster': 'file1', 'function': 'f', 'assistance': 'synthetic',
            'arm': 'production', 'seed': 0, 'exact': False, 'compiles': 3, 'budget': 4,
            'bundle_sha256': 'same', 'environment_sha256': 'env', 'valid': True,
            'baseline_gradient': [0, 5, 6], 'best_compiled_gradient': [0, 3, 4]}
    other = {**base, 'arm': 'production_diverse', 'best_compiled_gradient': [0, 2, 7]}
    comparison = runner().summarize([base, other])['comparisons'][0]
    assert comparison['gradient_pairs'] == 1 and comparison['gradient_better'] == 1
    mismatch = {**other, 'baseline_gradient': [0, 9, 9]}
    comparison = runner().summarize([base, mismatch])['comparisons'][0]
    assert comparison['gradient_pairs'] == 0 and comparison['gradient_unavailable'] == 1


def test_recorded_winner_inherits_header_assistance():
    result = runner().run_arm('f', 'root', Compiler(), arm='staged', budget=3, seed=0,
                             assistance='declared_unassisted',
                             proposals=[{'source_text': 'winner', 'assistance': 'header_assisted'}])
    assert result['exact'] and result['winner_assistance'] == 'header_assisted'
    assert result['phases'][-1]['lineage']['assistance'] == 'header_assisted'
