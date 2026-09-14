from dataclasses import dataclass
from solver.semantic_replay_cache import ReplayCache


@dataclass(frozen=True)
class Case:
    seed: int


def test_identical_inputs_reuse_execution_with_defensive_copies():
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        return [{'status': 'passed', 'trace': [1]}]
    cache = ReplayCache(run)
    kwargs = dict(cases=(Case(1),), call_arities={'f': 2}, return_registers=('v0',))
    first = cache.evaluate('target', 'candidate', **kwargs)
    first[0]['trace'].append(2)
    second = cache.evaluate('target', 'candidate', **kwargs)
    assert second[0]['trace'] == [1]
    second[0]['trace'].append(3)
    assert cache.evaluate('target', 'candidate', **kwargs)[0]['trace'] == [1]
    assert len(calls) == 1 and cache.summary()['hits'] == 2


def test_every_execution_input_invalidates_and_eviction_is_bounded():
    calls = []
    cache = ReplayCache(lambda *a, **kw: calls.append(1) or [], maximum=2)
    kwargs = dict(cases=(Case(1),), call_arities={}, return_registers=('v0',))
    for target, candidate, kw in [
        ('t', 'c', kwargs), ('t2', 'c', kwargs), ('t', 'c2', kwargs),
        ('t', 'c', {**kwargs, 'cases': (Case(2),)}),
        ('t', 'c', {**kwargs, 'call_arities': {'f': 1}}),
        ('t', 'c', {**kwargs, 'return_registers': ('f0',)}),
        ('t', 'c', kwargs),
    ]:
        cache.evaluate(target, candidate, **kw)
    assert len(calls) == 7 and len(cache.entries) == 2


def test_source_idiom_families_are_stable_across_local_names():
    from solver.transition_policy import action_family
    for family in ('absolute-difference-reuse', 'direct-field-divmod-chain',
                   'divmod-reuse', 'nested-divmod-reuse', 'masked-parameter-cast'):
        assert action_family('', family + ':a') == action_family('', family + ':b') == family
    assert action_family('', 'float-chain-reuse:0:1+counted-loop') == 'float-chain-counted-loop'
