import pytest
from solver import mips_differential as d


def test_instruction_budget_counts_all_trials_and_clamps_last_case():
    seeds=tuple(d.TestCase(str(i),i) for i in range(4))
    result=d.explore_coverage('loop:\naddiu v0,v0,1\nbne v0,zero,loop\nnop\njr ra\nnop',seeds,max_steps=7,max_total_steps=20)
    assert result.executed_steps==20
    assert [r.instruction_count for r in result.runs]==[7,7,6]
    assert result.trial_status_counts=={'step_limit':3}
    assert result.stop_reason.startswith('total instruction budget exhausted')
    assert result.to_dict()['max_total_steps']==20
    assert not result.report.complete


@pytest.mark.parametrize('budget',[0,-1,False,1.5])
def test_invalid_total_budget_rejected(budget):
    with pytest.raises(ValueError,match='positive total'):
        d.explore_coverage('jr ra\nnop',(d.TestCase('seed',1),),max_total_steps=budget)
