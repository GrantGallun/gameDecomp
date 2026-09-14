import pytest
import itertools
from types import SimpleNamespace
from solver import mips_differential as d


def test_memory_heavy_seed_cannot_starve_other_families_or_seeds(monkeypatch):
    monkeypatch.setattr(d,'_read_locations',lambda run:{('player',i,1) for i in range(10000)})
    run = SimpleNamespace(error='',trace=[],writes=[],
                          calls=[SimpleNamespace(callee='helper',ordinal=1)])
    seeds = (d.TestCase('first',1),d.TestCase('second',2))
    rows = list(itertools.islice(d._balanced_mutations(d.Program.parse('f','jr ra\nnop'),
        seeds,(run,run),mutable_entry_registers=('a1',),pointer_entry_registers=('a2',)),10))
    assert [index for index,case in rows] == [0,1]*5
    assert all(case.player_writes for _,case in rows[:2])
    assert all(case.entry_registers for _,case in rows[2:6])
    assert all(case.call_returns for _,case in rows[6:8])
    assert all(case.seed != seeds[index].seed for index,case in rows[8:])


def test_total_instructions_clamps_final_trial_and_retains_rejection():
    result = d.build_semantic_stress_panel('loop:\naddiu v0,v0,1\nbne v0,zero,loop\nnop\njr ra\nnop',
        (d.TestCase('seed', 1),), max_steps=7, max_total_steps=20, max_generated=10)
    assert result.executed_steps == 20
    assert result.attempted_cases == 3
    assert dict(result.rejected_status_counts) == {'step_limit': 3}
    assert not result.cases
    assert 'instruction budget exhausted' in result.stop_reasons


def test_trial_limit_counts_rejected_inputs_not_only_selected():
    result = d.build_semantic_stress_panel('lw v0,0(zero)\njr ra\nnop',
        (d.TestCase('seed', 1),), max_trials=3, max_generated=10)
    assert result.attempted_cases == 3
    assert dict(result.rejected_status_counts) == {'memory_fault': 3}
    assert 'trial budget exhausted' in result.stop_reasons


def test_generation_is_lazy_and_reports_unenumerated_dimensions(monkeypatch):
    generated = []
    def mutations(*args, **kwargs):
        for i in range(100):
            category = kwargs.get('category')
            offset = ['memory','scalar','pointer','call','filler'].index(category)*100
            generated.append((category,i))
            yield d.TestCase(str(i), offset+i+10)
    monkeypatch.setattr(d, '_iter_mutations', mutations)
    result = d.build_semantic_stress_panel('jr ra\nnop', (d.TestCase('seed', 1),),
        max_generated=3, max_cases=2)
    assert generated == [('memory',0), ('scalar',0), ('pointer',0)]
    assert result.examined_mutations == result.generated_cases == 3
    assert any('unenumerated' in reason for reason in result.stop_reasons)
    assert result.to_dict()['work_limits']['max_generated'] == 3


def test_seed_budget_debt_is_not_silently_discarded():
    result = d.build_semantic_stress_panel('jr ra\nnop',
        tuple(d.TestCase(str(i), i) for i in range(3)), max_trials=1)
    assert result.unattempted_seed_count == 2
    assert result.attempted_cases == 1


@pytest.mark.parametrize('value', [0, -1, False, 1.5])
@pytest.mark.parametrize('key', ['max_total_steps', 'max_trials', 'max_generated'])
def test_invalid_limits_decline(key, value):
    with pytest.raises(ValueError, match='positive integers'):
        d.build_semantic_stress_panel('jr ra\nnop', (d.TestCase('seed', 1),), **{key:value})
