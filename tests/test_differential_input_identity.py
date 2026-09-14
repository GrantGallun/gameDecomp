from dataclasses import replace

from solver import mips_differential as d


def test_fully_shadowed_writes_keep_effective_memory_and_raw_history():
    case = d.TestCase('history', 1,
        player_writes=((0, 4, 0x12345678), (1, 1, 9), (0, 4, 0xabcdef01)),
        global_writes=(('gInput', 4, 77), ('gInput+0x1', 1, 7), ('gInput', 4, 9)))
    compact = replace(case, player_writes=case.player_writes[1:],
                      global_writes=case.global_writes[1:])
    assert d._stress_input_identity(case) == d._stress_input_identity(compact)
    program = d.Program.parse('read',
        'lw v0,0(a0)\nlui t0,%hi(gInput)\nlw v1,%lo(gInput)(t0)\njr ra\nnop')
    runs = [d.execute_case(program, row, return_registers=('v0', 'v1'))
            for row in (case, compact)]
    assert all(run.status == 'returned' for run in runs)
    assert runs[0].return_values == runs[1].return_values == {'v0': 0xabcdef01, 'v1': 9}
    assert len(case.player_writes) == len(case.global_writes) == 3


def test_overlap_and_alias_order_remain_distinct():
    case = d.TestCase('overlap', 1,
        player_writes=((0, 4, 0x12345678), (1, 1, 9)),
        global_writes=(('gInput', 4, 1), ('gAlias', 4, 2)))
    assert d._stress_input_identity(case) != d._stress_input_identity(
        replace(case, player_writes=tuple(reversed(case.player_writes))))
    assert d._stress_input_identity(case) != d._stress_input_identity(
        replace(case, global_writes=tuple(reversed(case.global_writes))))
    assert d._stress_input_identity(case) != d._stress_input_identity(
        replace(case, global_writes=(('gInput', 4, 1), ('gAlias', 2, 2))))


def test_seed_register_and_call_inputs_remain_distinct():
    case = d.TestCase('seed', 1, player_writes=((0, 4, 1),))
    for changed in (replace(case, seed=2),
                    replace(case, entry_registers=(('a1', 2),)),
                    replace(case, call_returns=(('callee', 0, 1),))):
        assert d._stress_input_identity(case) != d._stress_input_identity(changed)


def test_stress_slots_are_distinct_inputs_and_wrong_candidate_still_fails():
    target = 'lw v0,0(a0)\njr ra\nnop'
    seed = d.TestCase('seed', 1, player_writes=((0, 4, 9), (0, 4, 0)))
    panel = d.build_semantic_stress_panel(target, (seed,),
        mutable_entry_registers=(), return_registers=('v0',),
        max_cases=12, max_trials=16, max_generated=128)
    assert len(panel.cases) == 12
    assert len({d._stress_input_identity(case) for case in panel.cases}) == 12
    correct = d.run_suite(target, target, panel.cases, return_registers=('v0',))
    wrong = d.run_suite(target, 'lw v0,0(a0)\naddiu v0,v0,1\njr ra\nnop',
                        panel.cases, return_registers=('v0',))
    assert all(row.status == 'passed' for row in correct)
    assert all(row.status == 'failed' for row in wrong)
