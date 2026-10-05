"""Generated paths must obey contracts without manufacturing observations."""
from copy import deepcopy
import importlib

import pytest


def engine():
    return importlib.import_module('solver.capability_potential')


def op(name, requires, produces, preserves=(), invalidates=()):
    return dict(id=name, requires=requires, produces=produces,
                preserves=list(preserves), invalidates=list(invalidates))


def paths(report, goal):
    return [p for p in report['paths'] if p['goal'] == goal]


def test_two_step_potential_is_generated_without_listing_a_composite_capability():
    report = engine().generate({'layout': True, 'named': False, 'wide': False}, [
        op('name_fields', {'layout': True}, {'named': True}, ['layout']),
        op('lift_wide', {'layout': True, 'named': True}, {'wide': True}),
    ], {'wide_candidate': {'wide': True}}, max_depth=2)
    p = paths(report, 'wide_candidate')
    assert len(p) == 1
    assert p[0]['via'] == ['name_fields', 'lift_wide']
    assert p[0]['conditions'] == []
    assert p[0]['status'] == 'supported-by-contracts'
    assert report['potential']['name_fields'][0]['via'] == p[0]['via']
    assert report['potential']['lift_wide'] == []
    assert p[0]['evidence_status'] == 'contract-inference'
    assert report['new_compiler_calls'] == 0


def test_unknown_prerequisite_is_retained_as_a_condition_not_observed_success():
    report = engine().generate({'layout': None}, [
        op('lift', {'layout': True}, {'wide': True}),
    ], {'wide': {'wide': True}})
    p = paths(report, 'wide')[0]
    assert p['status'] == 'conditional'
    assert [(c['predicate'], c['value']) for c in p['conditions']] == [('layout', True)]
    assert p['next_test']['action'] == 'establish-prerequisite'
    assert report['nodes'][0]['facts']['layout'] is None
    assert not report['global_impossibility_established']


def test_known_false_requirement_is_blocked_and_missing_producer_is_visible():
    report = engine().generate({'layout': False}, [
        op('lift', {'layout': True}, {'wide': True}),
    ], {'wide': {'wide': True}})
    assert paths(report, 'wide') == []
    gap = next(b for b in report['blocked'] if b['operation'] == 'lift')['gaps'][0]
    assert gap == {'predicate': 'layout', 'required': True, 'actual': False, 'producers': []}
    assert report['goals']['wide']['status'] == 'no-path-within-model-and-bounds'


def test_alternative_representations_never_pool_facts_into_a_fictional_path():
    report = engine().generate({'seed': True}, [
        op('left', {'seed': True}, {'left': True, 'right': False}, ['seed']),
        op('right', {'seed': True}, {'left': False, 'right': True}, ['seed']),
        op('join', {'left': True, 'right': True}, {'done': True}),
    ], {'done': {'done': True}}, max_depth=3)
    # Root cannot certify either representation; root join is conditional, never
    # evidence that the mutually exclusive concrete alternatives composed.
    assert all(p['via'] == ['join'] for p in paths(report, 'done'))
    assert all(p['conditions'] for p in paths(report, 'done'))
    assert not report['potential']['left'] and not report['potential']['right']


def test_preserved_unknown_assumption_cannot_later_be_assumed_opposite():
    report = engine().generate({'switch': None}, [
        op('choose', {'switch': True}, {'chosen': True}, ['switch']),
        op('contradict', {'chosen': True, 'switch': False}, {'done': True}),
    ], {'done': {'done': True}}, max_depth=2)
    assert not any(p['via'] == ['choose', 'contradict'] for p in report['paths'])
    child = next(n for n in report['nodes'] if n['via'] == ['choose'])
    assert child['facts']['switch'] is True
    assert child['conditions'][0]['predicate'] == 'switch'


@pytest.mark.parametrize('explicit_invalidation', [False, True])
def test_stale_fact_is_forgotten_unless_preserved(explicit_invalidation):
    change = op('change', {}, {'edited': True}, invalidates=['checked'] if explicit_invalidation else [])
    report = engine().generate({'checked': True}, [change,
        op('use_check', {'edited': True, 'checked': True}, {'done': True}),
    ], {'done': {'done': True}}, max_depth=2)
    p = next(p for p in paths(report, 'done') if p['via'] == ['change', 'use_check'])
    assert p['status'] == 'conditional'
    assert any(c['predicate'] == 'checked' and c['step'] == 1 for c in p['conditions'])


def test_unestablished_goal_is_not_assumed_true():
    report = engine().generate({'goal': None}, [], {'goal': {'goal': True}})
    assert not report['paths']
    assert report['goals']['goal']['gaps'][0]['producers'] == []


def test_depth_and_node_limits_remain_explicit_without_impossibility_claim():
    operations = [op('first', {}, {'a': True}), op('second', {'a': True}, {'b': True})]
    report = engine().generate({'a': False, 'b': False}, operations, {'b': {'b': True}}, max_depth=1)
    assert not report['paths'] and report['limits']['depth_cutoff']
    small = engine().generate({}, operations, {'b': {'b': True}}, max_nodes=1)
    assert len(small['nodes']) == 1 and small['limits']['node_cutoff']
    assert not small['global_impossibility_established']


def test_repeated_identical_state_does_not_expand_cycles():
    report = engine().generate({'a': True}, [op('noop', {'a': True}, {}, ['a'])],
                               {'absent': {'absent': True}}, max_depth=50)
    assert len(report['nodes']) == 1
    assert not report['limits']['node_cutoff']


@pytest.mark.parametrize('bad', [
    op('bad', {}, {'a': True}, ['a']),
    op('bad', {}, {'a': True}, invalidates=['a']),
    op('bad', {}, {}, ['a'], ['a']),
    op('bad', {'a': None}, {}),
])
def test_inconsistent_or_unknown_contract_assignments_are_rejected(bad):
    with pytest.raises(ValueError):
        engine().generate({}, [bad], {'a': {'a': True}})


def test_rehashed_derived_potential_cannot_bypass_reconstruction():
    from eval.search_replay import digest
    report = engine().generate({}, [op('emit', {}, {'a': True})], {'a': {'a': True}})
    assert engine().validate(deepcopy(report)) == report
    report['paths'][0]['status'] = 'verified-exact'
    report['sha256'] = digest({k: v for k, v in report.items() if k != 'sha256'})
    with pytest.raises(ValueError, match='differs'):
        engine().validate(report)


def real_assessment(*, caller='compile-recovery', byteview=False, layout=False):
    from test_capability_map import assess
    arg = '(*(u32 *)(p+16))' if byteview else 'p->unk10'
    return assess(source='void f(void) { sink(/* u64+0x0 */ '+arg+'); }', connected=caller,
                  facts={'header_declarations': True, 'target_available': True,
                         'type_plan_domain_supported': True, 'layout_probe_available': True,
                         'closed_wide_idiom': True, 'measured_layouts': layout})


def test_real_contracts_generate_measurement_to_wide_constructor_path():
    provider = importlib.import_module('solver.capability_operations')
    a = real_assessment()
    report = provider.from_assessment(a, max_depth=2)
    chain = next(p for p in paths(report, 'wide_operations')
                 if p['via'] == ['type_layouts', 'wide_operations'])
    assert chain['conditions'] == []
    assert chain['evidence_status'] == 'contract-inference'
    assert report['inputs']['context']['assessment_sha256'] == a['sha256']
    assert report['inputs']['context']['source_sha256'] == a['source_sha256']
    assert engine().validate(report) == report
    assert 'exact_c' not in report['goals']


@pytest.mark.parametrize('caller,byteview,gap', [
    ('theory', False, 'wired.wide_operations'),
    ('compile-recovery', True, 'named_wide_fields'),
])
def test_real_wiring_and_representation_gaps_are_not_invented_away(caller, byteview, gap):
    provider = importlib.import_module('solver.capability_operations')
    report = provider.from_assessment(real_assessment(caller=caller, byteview=byteview), max_depth=2)
    root = next(b for b in report['blocked'] if b['node'] == 'root' and b['operation'] == 'wide_operations')
    assert any(g['predicate'] == gap and g['producers'] == [] for g in root['gaps'])
    # Other source mutations may leave representation unknown; any such route
    # must explicitly admit that uncertainty instead of claiming a conversion.
    if byteview:
        assert all(p['conditions'] for p in paths(report, 'wide_operations'))
    else:
        assert not paths(report, 'wide_operations')


def test_source_mutation_invalidates_compile_and_feedback_without_changing_target():
    provider = importlib.import_module('solver.capability_operations')
    a = real_assessment(layout=True)
    report = provider.from_assessment(a, max_depth=1)
    child = next(n for n in report['nodes'] if n['via'] == ['wide_operations'])
    assert child['facts']['compiled_candidate'] is False
    assert child['facts']['source_bound_feedback'] is False
    assert child['facts']['named_wide_fields'] is None
    assert child['facts']['target_available'] is True
    assert not any(p['goal'] == 'object_check' for p in report['paths'])


def test_an_assumed_goal_cannot_be_preserved_into_an_achievement():
    report = engine().generate({'goal': None}, [
        op('circular', {'goal': True}, {'ran': True}, ['goal']),
    ], {'goal': {'goal': True}})
    assert not paths(report, 'goal')


def test_reasserting_the_same_required_assumption_does_not_establish_a_goal():
    report = engine().generate({'goal': None}, [
        op('circular', {'goal': True}, {'goal': True, 'ran': True}),
    ], {'goal': {'goal': True}})
    assert not paths(report, 'goal')


def test_planner_records_generated_potential_without_extra_compiler_calls():
    from eval.theory_planner import TheoryOnline
    from eval.repair_planner import run_planner, replay_planner
    from test_repair_planner import empty_model
    from test_search_scheduler import compiler, context
    from test_capability_map import ASM, modules
    m, c = modules()
    callback, receipts = compiler({'start': -1})
    ctx = context()
    def assessor(source, verdict):
        return m.assess(source, 'f', verdict, assembly=ASM, context=ctx,
                        contracts=c.catalog(), facts={}, connected='theory')
    def factory(*args, **kwargs):
        return TheoryOnline(*args, **kwargs, inspect_routes=lambda *_: [],
                            capability_assessor=assessor, capability_potential=True)
    env = factory('start', callback, lambda *_: iter(()), ctx)
    report = run_planner(env, empty_model(), budget=1)
    generated = report['theory']['capability_envelope']['potential']['root']
    assert engine().validate(generated) == generated
    assert len(receipts) == 1
    assert report == replay_planner(env.world, empty_model(), lambda *_: iter(()), budget=1,
                                    environment_factory=factory)


def test_planner_potential_requires_source_bound_assessor():
    from eval.theory_planner import TheoryOnline
    from test_search_scheduler import compiler, context
    callback, _ = compiler({'start': -1})
    with pytest.raises(ValueError, match='assessor'):
        TheoryOnline('start', callback, lambda *_: iter(()), context(), inspect_routes=lambda *_: [],
                     capability_potential=True)


def test_cli_exports_selected_bound_assessment_and_refuses_overwrite(tmp_path):
    import json
    from pathlib import Path
    import subprocess
    import sys
    input_path, output_path = tmp_path/'input.json', tmp_path/'potential.json'
    a = real_assessment()
    input_path.write_text(json.dumps({'assessments': {'selected': a}}))
    command = [sys.executable, '-m', 'eval.capability_potential', '--assessment', str(input_path),
               '--node', 'selected', '--output', str(output_path), '--max-depth', '2']
    result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    exported = json.loads(output_path.read_text())
    assert engine().validate(exported) == exported
    assert exported['inputs']['context']['assessment_sha256'] == a['sha256']
    assert any(p['via'] == ['type_layouts', 'wide_operations'] for p in paths(exported, 'wide_operations'))
    original = output_path.read_bytes()
    again = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert again.returncode != 0
    assert output_path.read_bytes() == original
