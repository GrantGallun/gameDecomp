from dataclasses import replace
from eval import semantic_lane
from solver import mips_differential as d


ASM = '''addiu sp,sp,-24
sw ra,20(sp)
li a0,7
jal unknown
li a2,1
lw ra,20(sp)
addiu sp,sp,24
jr ra
nop'''


def run():
    return d.run_suite(ASM, ASM.replace('li a2,1', 'li a2,9'),
        (d.TestCase('unknown', 1),), call_arities={'unknown': 4})[0]


def test_only_guessed_words_are_inconclusive_not_pass():
    row = run()
    assert row.status == 'failed'
    result = semantic_lane.classify_unknown_direct_arguments(row, {'unknown': {'arity_known': False}})
    assert result.status == 'inconclusive'
    assert result.first_divergence == row.first_divergence
    assert result.target is row.target
    assert semantic_lane.outcome_accounting([result])['status'] == 'inconclusive'


def test_known_or_missing_contract_still_fails():
    row = run()
    for contracts in ({}, {'unknown': {'arity_known': True}}):
        assert semantic_lane.classify_unknown_direct_arguments(row, contracts) is row


def test_other_disagreements_are_not_excused():
    row = run()
    for reason in ('final persistent memory differs', 'declared return registers differ',
                   'candidate violates callee-saved ABI'):
        modified = replace(row, reasons=(*row.reasons, reason))
        assert semantic_lane.classify_unknown_direct_arguments(modified, {'unknown': {'arity_known': False}}) is modified
    call = replace(row.candidate.calls[0], checkpoint_digest='different')
    candidate = replace(row.candidate, calls=(call,))
    modified = replace(row, candidate=candidate)
    assert semantic_lane.classify_unknown_direct_arguments(modified, {'unknown': {'arity_known': False}}) is modified
