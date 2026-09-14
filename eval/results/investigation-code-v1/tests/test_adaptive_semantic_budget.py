from eval import dag_pipeline_pilot as dag, semantic_lane
from solver import callee_execution, mips_differential as d, workspace


def panel_fixture(monkeypatch, tmp_path, assembly, steps=8):
    (tmp_path/'target_object_dump_normalized.s').write_text(assembly)
    monkeypatch.setattr(workspace, 'semantic_assembly', lambda text, obj: text)
    monkeypatch.setattr(dag, 'prototype_info', lambda *a: {
        'return_registers': ['v0'], 'mutable_scalar_registers': [], 'pointer_registers': []})
    seeds = (d.TestCase('original', 1),)
    monkeypatch.setattr(dag, '_seed_cases', lambda *a: seeds)
    monkeypatch.setattr(dag, '_call_contracts', lambda *a: ({}, {}))
    calls = []
    explore = d.explore_coverage
    def record(target, inputs, **kwargs):
        result = explore(target, inputs, **kwargs)
        calls.append((inputs, kwargs, result))
        return result
    monkeypatch.setattr(d, 'explore_coverage', record)
    panel = semantic_lane.Panel(tmp_path, tmp_path, 'f', max_cases=1,
        max_steps=steps, exploration_cases=1, callee_environment=callee_execution.Environment({}))
    return panel, calls


def test_long_loop_retries_original_target_inputs_and_retains_both_phases(monkeypatch, tmp_path):
    panel, calls = panel_fixture(monkeypatch, tmp_path,
        'li v0,10\nloop:\naddiu v0,v0,-1\nbne v0,zero,loop\nnop\njr ra\nnop')
    assert len(calls) == 2
    first, second = calls
    assert first[1]['max_total_steps'] == 128
    assert second[0] == first[2].cases
    assert second[1]['max_steps'] == panel.max_steps == 128
    assert second[1]['max_total_steps'] == 1024-first[2].executed_steps
    phases = panel.report['exploration_budget_phases']
    assert len(phases) == 2
    assert phases[0]['exploration'] == first[2].to_dict()
    assert phases[1]['exploration'] == second[2].to_dict()
    assert first[2].trial_status_counts == {'step_limit': 1}
    assert panel.cases
    assert any('earlier noncompleted' in debt for debt in panel.report['debt'])


def test_completed_target_does_not_raise_step_limit(monkeypatch, tmp_path):
    panel, calls = panel_fixture(monkeypatch, tmp_path, 'li v0,7\njr ra\nnop')
    assert len(calls) == 1
    assert panel.max_steps == 8
    assert len(panel.report['exploration_budget_phases']) == 1


def test_unsupported_target_does_not_trigger_timeout_retry(monkeypatch, tmp_path):
    panel, calls = panel_fixture(monkeypatch, tmp_path, 'li t0,0\ndivu zero,t0,t0\njr ra\nnop')
    assert len(calls) == 1
    assert not panel.cases
    assert panel.report['target_execution']['status_counts'] == {'unsupported': 1}
