from solver import mips_differential as d, modelrepair
from eval import semantic_lane
from types import SimpleNamespace


TARGET = '''addiu sp,sp,-40
sw ra,36(sp)
li t9,0x12345678
li a0,7
li t0,16
sw t0,16(sp)
jalr t9
li a1,9
lw ra,36(sp)
addiu sp,sp,40
jr ra
nop'''


def test_unknown_indirect_arity_exposes_ignored_words_after_delay_slot():
    candidate = TARGET.replace('li a0,7','li a0,8').replace('li t0,16','li t0,128')
    result = d.run_suite(TARGET,candidate,(d.TestCase('window',1),),return_registers=())[0]
    # The old coverage hook still compares equal: observations must NOT become
    # guessed argument constraints, synthetic return hashes, or failed gates.
    assert result.status == 'passed'
    call = result.target.calls[0]
    assert not call.arity_known and call.raw_arguments == ()
    assert call.abi_observations[1]['value'] == 9
    assert call.abi_observations[4]['value'] == 16
    assert call.to_dict()['arity_known'] is False
    rows = semantic_lane.indirect_call_obligations(result)
    assert len(rows) == 1
    assert [x['word'] for x in rows[0]['unclassified_abi_word_differences']] == [0,4]
    assert 'not an argument contract' in rows[0]['authority']


def test_equal_unknown_words_still_leave_an_obligation():
    result = d.run_suite(TARGET,TARGET,(d.TestCase('same',1),),return_registers=())[0]
    rows = semantic_lane.indirect_call_obligations(result)
    assert len(rows) == 1 and rows[0]['unclassified_abi_word_differences'] == []


def test_known_direct_zero_argument_call_is_not_unknown():
    assembly = TARGET.replace('jalr t9','jal callback')
    result = d.run_suite(assembly,assembly,(d.TestCase('direct',1),),call_arities={'callback':0},return_registers=())[0]
    assert result.target.calls[0].arity_known
    assert semantic_lane.indirect_call_obligations(result) == []


def test_semantic_prompt_retains_bounded_indirect_diagnostic(monkeypatch,tmp_path):
    from solver import compile_obligations
    monkeypatch.setattr(compile_obligations,'header_types',lambda *a,**kw:[])
    state=SimpleNamespace(source='void f(void) {}',semantic={
        'indirect_call_obligations':[{'kind':'indirect-call-arity-unmodeled','word':i} for i in range(9)]})
    prompt=modelrepair.semantic_prompt(tmp_path,'f',state,'jr ra\nnop',{},[])
    assert prompt.count('indirect-call-arity-unmodeled') == 4
    assert 'not ABI facts' in prompt
