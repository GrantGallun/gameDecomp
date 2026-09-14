"""Closed helper execution checked against independent 32-bit instruction paths."""
import pytest

from solver import callee_execution as c, mips_differential as d


def inline_shift(operation, count):
    n = count & 63
    if n == 0:
        body = 'move v0,a0\nmove v1,a1'
    elif operation == 'shift_left':
        body = (f'sll v0,a0,{n}\nsrl t0,a1,{32-n}\nor v0,v0,t0\nsll v1,a1,{n}' if n < 32
                else f'sll v0,a1,{n-32}\nmove v1,zero')
    else:
        high = 'sra' if operation == 'shift_right_signed' else 'srl'
        body = (f'srl v1,a1,{n}\nsll t0,a0,{32-n}\nor v1,v1,t0\n{high} v0,a0,{n}' if n < 32
                else f'{high} v1,a0,{n-32}\n' + ('sra v0,a0,31' if high == 'sra' else 'move v0,zero'))
    return body + '\njr ra\nnop'


CALLER = '''addiu sp,sp,-32
sw ra,20(sp)
jal helper
nop
lw ra,20(sp)
addiu sp,sp,32
jr ra
nop'''


@pytest.mark.parametrize('operation', sorted(c.WORD_PAIR_SHIFTS))
@pytest.mark.parametrize('value', [0, 1, 0xffffffff, 0x123456789abcdef0, 0x8000000000000000, 0xffffffffffffffff])
@pytest.mark.parametrize('count', [0, 1, 31, 32, 33, 63, 64, 65, 0x100000021, 0xffffffffffffffff])
def test_closed_shifts_match_inline_32_bit_paths(operation, value, count):
    leaf = c.Leaf(c.WORD_PAIR_SHIFTS[operation], 'synthetic instruction control')
    assert leaf.word_pair_operation == operation and not leaf.word_pair_multiply
    case = d.TestCase('shift', 1, entry_registers=(('a0', value >> 32), ('a1', value & 0xffffffff),
                                                 ('a2', count >> 32), ('a3', count & 0xffffffff)))
    row = d.run_suite(inline_shift(operation, count), CALLER, (case,), call_arities={'helper': 4},
        return_registers=('v0', 'v1'), callee_environment=c.Environment({'helper': leaf}))[0]
    assert row.status == 'passed', row.reasons
    expected = (value << (count & 63) if operation == 'shift_left' else
                value >> (count & 63) if operation == 'shift_right_unsigned' else
                d.sign_extend(value, 64) >> (count & 63)) & ((1 << 64) - 1)
    assert row.candidate.return_values == {'v0': expected >> 32, 'v1': expected & 0xffffffff}
    assert row.candidate.concrete_calls[0]['execution']['instruction_count'] == 11


@pytest.mark.parametrize('replacement', ['ld t7,16(sp)', 'ld t7,0(sp)', 'dsllv v0,t7,t6', 'dsllv v0,t6,a3'])
def test_changed_streams_are_not_admitted_as_closed_shifts(replacement):
    stream = c.WORD_PAIR_SHIFTS['shift_left']
    original = 'ld t7,8(sp)' if replacement.startswith('ld') else 'dsllv v0,t6,t7'
    with pytest.raises(ValueError):
        c.Leaf(stream.replace(original, replacement), 'synthetic')
    with pytest.raises(ValueError, match='unsupported word-pair'):
        c.WordPairRunner(d.Program.parse('malformed', stream.replace(original, replacement)))


def test_shift_source_contract_names_shift_instead_of_product():
    leaf = c.Leaf(c.WORD_PAIR_SHIFTS['shift_right_signed'], 'synthetic')
    row, = c.source_contracts('s32 helper(s64, s64);', c.Environment({'helper': leaf}))
    assert row['status'] == 'result-width-conflict'
    assert row['binary_operation'] == 'shift_right_signed'
    assert row['binary_result_mapping']['v1'] == 'shift result bits 31..0'
    assert row['binary_argument_mapping']['a0:a1'] == 'signed 64-bit word pair'


@pytest.mark.grounded
def test_real_shift_helpers_require_rom_bytes_and_reassembly(repo_path):
    environment, report = c.load_binary_leaves(repo_path, c.COMPILER_WORD_PAIR_HELPERS)
    assert set(environment.leaves) == set(c.COMPILER_WORD_PAIR_HELPERS), report
    assert {leaf.word_pair_operation for leaf in environment.leaves.values()} == {'multiply', *c.WORD_PAIR_SHIFTS}
    assert all(r['status'] == 'executable' for r in report)


def test_panel_closure_precedes_exploration_and_never_trusts_helper_name(tmp_path, monkeypatch):
    from eval import dag_pipeline_pilot as dag, semantic_lane
    (tmp_path / 'target_object_dump_normalized.s').write_text('jr ra\nnop')
    monkeypatch.setattr(dag, 'prototype_info', lambda *a, **kw: {
        'return_registers': ['v0'], 'mutable_scalar_registers': [], 'issues': []})
    monkeypatch.setattr(dag, '_call_contracts', lambda *a, **kw: ({}, {}))
    calls = []
    def load(repo, names, contracts):
        calls.append(names)
        # A same-named 32-bit function is not evidence of a compiler-helper ABI.
        return c.Environment({'__ll_lshift': c.Leaf(c.WORD_PAIR_SHIFTS['shift_left'], 'synthetic'),
                              '__ull_rshift': c.Leaf('jr ra\nnop', 'synthetic')}), []
    monkeypatch.setattr(c, 'load_binary_leaves', load)
    real_explore = d.explore_coverage
    def explore(*args, **kwargs):
        assert kwargs['call_arities']['__ll_lshift'] == 4
        assert '__ull_rshift' not in kwargs['callee_environment'].leaves
        return real_explore(*args, **kwargs)
    monkeypatch.setattr(d, 'explore_coverage', explore)
    panel = semantic_lane.Panel(tmp_path, tmp_path, 'f', max_cases=1, exploration_cases=1)
    assert calls == [sorted(c.COMPILER_WORD_PAIR_HELPERS)]
    assert panel.call_contracts['__ll_lshift']['return_registers'] == ['v0', 'v1']
    assert panel.report['callee_environment']['leaves']['__ll_lshift']['word_pair_operation'] == 'shift_left'
    assert panel.report['callee_admission'][0]['callee'] == '__ull_rshift'
    assert '__ull_rshift' not in panel.arities
    assert panel.report['comparison_policy'].endswith('v2-opaque-call-ordinals')


NEUTRAL_HELPER = 'li a0,0\nli a1,1\nli a2,0\nli a3,0\njal helper\nnop\n'


def opaque_caller(helpers=False):
    call = 'li a0,7\njal probe\nnop\n'
    helper = NEUTRAL_HELPER if helpers else ''
    return ('addiu sp,sp,-48\nsw ra,44(sp)\n' + helper + call + helper + call
            + 'lw ra,44(sp)\naddiu sp,sp,48\njr ra\nnop')


def shift_environment(**kwargs):
    return c.Environment({'helper': c.Leaf(c.WORD_PAIR_SHIFTS['shift_left'], 'synthetic')}, **kwargs)


@pytest.mark.parametrize('overrides', [(), (('probe', 0, 0x77), ('probe', 1, 0x88))])
def test_candidate_only_helpers_do_not_shift_opaque_returns_clobbers_or_overrides(overrides):
    case = d.TestCase('opaque', 7, call_returns=overrides)
    kwargs = dict(call_arities={'helper': 4, 'probe': 1}, return_registers=('v0','v1'),
                  callee_environment=shift_environment())
    row = d.run_suite(opaque_caller(), opaque_caller(True), (case,), **kwargs)[0]
    assert row.status == 'passed', row.reasons
    assert [call.ordinal for call in row.target.calls] == [0, 1]
    assert [call.ordinal for call in row.candidate.calls] == [0, 1]
    assert [call['ordinal'] for call in row.candidate.concrete_calls] == [0, 2]
    if overrides:
        assert [call.returned for call in row.candidate.calls] == [0x77, 0x88]
    wrong = opaque_caller(True).replace('li a0,7', 'li a0,8', 1)
    assert d.run_suite(opaque_caller(), wrong, (case,), **kwargs)[0].status == 'failed'


def test_generated_opaque_return_mutations_apply_after_candidate_helpers():
    target, candidate = opaque_caller(), opaque_caller(True)
    environment = shift_environment()
    kwargs = dict(call_arities={'helper': 4, 'probe': 1}, return_registers=('v0','v1'),
                  callee_environment=environment)
    seed = d.TestCase('seed', 7)
    program = d.Program.parse('target', target)
    run = d.execute_case(program, seed, **kwargs)
    cases = list(d._iter_mutations(program, seed, run, mutable_entry_registers=(), category='call'))
    assert {(callee, ordinal) for case in cases for callee, ordinal, value in case.call_returns} == {('probe',0),('probe',1)}
    for case in cases:
        row = d.run_suite(target, candidate, (case,), **kwargs)[0]
        assert row.status == 'passed'
        for callee, ordinal, value in case.call_returns:
            assert row.candidate.calls[ordinal].callee == callee
            assert row.candidate.calls[ordinal].returned == value


def test_output_buffer_uses_opaque_ordinal_after_candidate_helper():
    prefix = 'addiu sp,sp,-48\nsw ra,44(sp)\n'
    body = ('addiu a0,sp,24\njal fill\nnop\nlw v0,24(sp)\nlw ra,44(sp)\n'
            'addiu sp,sp,48\njr ra\nnop')
    case = d.TestCase('output', 42, call_returns=(('fill',0,0),))
    row = d.run_suite(prefix+body, prefix+NEUTRAL_HELPER+body, (case,),
        call_arities={'helper':4,'fill':1}, return_registers=('v0',),
        callee_environment=shift_environment(outputs={'fill':c.OutputBuffer(0,4,'synthetic test')}))[0]
    assert row.status == 'passed' and row.candidate.calls[0].returned == 0
    assert row.target.return_values == row.candidate.return_values


def test_opaque_intervention_index_cannot_replace_a_concrete_helper():
    case = d.TestCase('diagnostic', 1)
    environment = shift_environment()
    target = d.execute_case(d.Program.parse('target', opaque_caller()), case,
        call_arities={'helper':4,'probe':1}, callee_environment=environment)
    program = d.Program.parse('candidate', opaque_caller(True))
    symbols = d.SymbolTable(set())
    runner = d.Runner(program, d._seed_memory(symbols, case), d._seed_registers(case), symbols,
        call_arities={'helper':4,'probe':1}, callee_environment=environment,
        call_interventions={0:target.calls[0]})
    run = runner.execute()
    assert run.status == 'unsupported'
    assert 'concrete callees do not support diagnostic interventions' in run.error
    assert not run.calls and not run.concrete_calls
