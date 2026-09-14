import pytest

from solver import callee_execution as c, mips_differential as d


def caller(offset=24, value=7, call='readWord', post=''):
    return f'''addiu sp,sp,-64
sw ra,20(sp)
li t0,{value}
sw t0,{offset}(sp)
addiu a0,sp,{offset}
jal {call}
nop
{post}
lw ra,20(sp)
addiu sp,sp,64
jr ra
nop'''


def compare(left, right, leaves=None, outputs=None, arities=None):
    environment = c.Environment(leaves or {}, outputs or {})
    return d.run_suite(left,right,(d.TestCase('test',42),),
        call_arities=arities or {'readWord':1},return_registers=('v0',),
        callee_environment=environment)[0]


def test_real_leaf_reads_accept_relocation_but_reject_wrong_contents():
    from eval.semantic_lane import callee_feedback
    leaf = c.Leaf('lw v0,0(a0)\njr ra\nnop','synthetic control')
    for offset,value,status in [(24,7,'passed'),(28,7,'passed'),(24,8,'failed')]:
        row = compare(caller(),caller(offset,value),{'readWord':leaf})
        assert row.status == status
        assert row.target.concrete_calls[0]['execution']['return_values'] == {'v0':'0x00000007'}
        assert not row.target.calls  # Internal arguments are not opaque observations.
        assert len(row.target.trace) == 12  # Includes one call-result boundary, not child indices.
        boundaries = [e for e in row.target.trace if e.effect.startswith('concrete callee')]
        assert any(n=='v0' and value==7 and origin.startswith('concrete readWord')
                   for n,value,origin in boundaries[0].writes)
        feedback = callee_feedback(row.target)
        assert feedback['calls'][0]['execution']['return_values'] == {'v0':'0x00000007'}
        assert len(feedback['calls'][0]['instruction_prefix']) <= 6


def test_leaf_shared_memory_aliasing_and_output_effects():
    leaf = c.Leaf('li t0,9\nsw t0,0(a0)\nlw v0,0(a1)\njr ra\nnop','synthetic alias control')
    left = caller().replace('jal readWord','move a1,a0\njal readWord')
    # Aliases relocated together work; distinct pointees cannot be conflated.
    right = caller(28).replace('jal readWord','move a1,a0\njal readWord')
    assert compare(left,right,{'readWord':leaf},arities={'readWord':2}).status == 'passed'
    wrong = right.replace('move a1,a0','addiu a1,sp,24\nsw zero,24(sp)')
    assert compare(left,wrong,{'readWord':leaf},arities={'readWord':2}).status == 'failed'


def test_stack_address_escape_and_address_sensitive_leaf_are_not_ignored():
    leaf = c.Leaf('move v0,a0\njr ra\nnop','synthetic address-sensitive control')
    assert compare(caller(),caller(28),{'readWord':leaf}).status == 'failed'
    leaf = c.Leaf('sw a0,0(a1)\nmove v0,zero\njr ra\nnop','synthetic pointer escape')
    left = caller().replace('jal readWord','li a1,0x10000000\njal readWord')
    right = caller(28).replace('jal readWord','li a1,0x10000000\njal readWord')
    assert compare(left,right,{'readWord':leaf},arities={'readWord':2}).status == 'failed'


def test_uninitialized_or_out_of_frame_reads_never_pass():
    leaf = c.Leaf('lw v0,4(a0)\njr ra\nnop','synthetic read')
    row = compare(caller(),caller(),{'readWord':leaf})
    assert row.status == 'inconclusive' and 'uninitialized' in row.target.error
    good = caller().replace('sw t0,24(sp)','sw t0,24(sp)\nsw t0,28(sp)')
    row = compare(good,caller(60),{'readWord':leaf})
    assert row.status == 'failed' and 'escapes active caller frame' in row.candidate.error


def test_output_model_writes_data_checks_frame_and_refuses_unresolved_aliases():
    effect = c.OutputBuffer(0,8,'explicit synthetic test environment')
    # Force success, independently of the ordinary opaque-return hash.
    case = d.TestCase('output',1,call_returns=(('fill',0,0),))
    environment = c.Environment(outputs={'fill':effect})
    def run(right):
        return d.run_suite(caller(call='fill',post='lw v0,28(sp)'),right,(case,),
            call_arities={'fill':1},return_registers=('v0',),callee_environment=environment)[0]
    assert run(caller(28,call='fill',post='lw v0,32(sp)')).status == 'passed'
    assert run(caller(60,call='fill',post='lw v0,64(sp)')).status == 'failed'
    assert run(caller(call='fill',post='sw zero,28(sp)\nlw v0,28(sp)')).status == 'failed'
    ambiguous = caller(call='fill').replace('jal fill','move a1,a0\njal fill')
    row = d.run_suite(ambiguous,ambiguous,(case,),call_arities={'fill':2},callee_environment=environment)[0]
    assert row.status == 'inconclusive' and 'aliasing' in row.target.error


@pytest.mark.parametrize('assembly',['jal other\nnop\njr ra\nnop','jr t0\nnop',
    'lui t0,%hi(global)\njr ra\nnop','mtc0 a0,$12\njr ra\nnop'])
def test_unsupported_callees_decline(assembly):
    with pytest.raises(ValueError):
        c.Leaf(assembly,'synthetic')


@pytest.mark.parametrize('left,right', [(0,0), (1,7), (0xffffffff,0xffffffff),
    (0x100000001,0x200000003), (0xffffffffffffffff,2), (0x8000000000000000,3)])
def test_word_pair_leaf_preserves_both_halves_and_overflow(left,right):
    leaf = c.Leaf(c.WORD_PAIR_MULTIPLY,'synthetic instruction control')
    assert leaf.word_pair_multiply and leaf.return_registers == ('v0','v1')
    body = caller(call='multiply').replace('jal multiply',
        f'li a0,{left >> 32}\nli a1,{left & 0xffffffff}\nli a2,{right >> 32}\nli a3,{right & 0xffffffff}\njal multiply')
    row = d.run_suite(body,body,(d.TestCase('pair',1),),call_arities={'multiply':4},
        return_registers=('v0','v1'),callee_environment=c.Environment({'multiply':leaf}))[0]
    expected = (left*right) & ((1<<64)-1)
    assert row.status == 'passed'
    assert row.target.return_values == {'v0':expected >> 32, 'v1':expected & 0xffffffff}
    assert row.target.concrete_calls[0]['execution']['instruction_count'] == 12


def test_word_pair_dialect_does_not_admit_arbitrary_64_bit_code():
    with pytest.raises(ValueError):
        c.Leaf(c.WORD_PAIR_MULTIPLY.replace('ld t7,8(sp)','ld t7,16(sp)'), 'synthetic')


def test_source_contract_exposes_lost_word_before_arithmetic_repairs():
    env = c.Environment({'renamed':c.Leaf(c.WORD_PAIR_MULTIPLY,'synthetic')})
    source = 's32 renamed(s32, s32, s32, s32);\nvoid f(void) { u64 x = (u64) renamed(0,1,0,2); }'
    rows = c.source_contracts(source,env)
    assert len(rows) == 1 and rows[0]['status'] == 'result-width-conflict'
    assert rows[0]['declared_result_words'] == 1
    assert rows[0]['binary_result_mapping']['v1'] == 'product bits 31..0'
    assert c.source_contracts(source.replace('s32 renamed','u64 renamed'),env)[0]['status'] == 'result-width-compatible'
    assert not c.source_contracts('/* '+source+' */',env)
    assert not c.source_contracts(source.replace('s32 renamed','UnknownType renamed'),env)


@pytest.mark.grounded
def test_real_word_pair_helper_requires_rom_reassembly(repo_path):
    environment, report = c.load_binary_leaves(repo_path,['__ll_mul'])
    assert '__ll_mul' in environment.leaves, report
    assert environment.leaves['__ll_mul'].word_pair_multiply
    assert environment.manifest()['leaves']['__ll_mul']['argument_words'] == 4


def test_leaf_budget_does_not_silently_fall_back_to_opaque():
    leaf = c.Leaf('.Lloop:\naddiu t0,t0,1\nb .Lloop\nnop','synthetic loop')
    row = d.run_suite(caller(),caller(),(d.TestCase('budget',1),),call_arities={'readWord':1},
        max_steps=24,callee_environment=c.Environment({'readWord':leaf}))[0]
    assert row.status == 'inconclusive' and 'step_limit' in row.target.error


def test_target_exploration_and_stress_use_same_environment():
    leaf = c.Leaf('lw v0,0(a0)\njr ra\nnop','synthetic')
    kwargs = dict(call_arities={'readWord':1},callee_environment=c.Environment({'readWord':leaf}))
    cases = (d.TestCase('seed',2),)
    explored = d.explore_coverage(caller(),cases,max_cases=4,**kwargs)
    panel = d.build_semantic_stress_panel(caller(),cases,max_cases=4,**kwargs)
    assert all(r.concrete_calls for r in (*explored.runs,*panel.runs))


def test_output_overlap_with_saved_return_address_is_not_hidden():
    environment = c.Environment(outputs={'fill':c.OutputBuffer(0,8,'synthetic')})
    case = d.TestCase('overlap',1,call_returns=(('fill',0,0),))
    row = d.run_suite(caller(call='fill'),caller(20,call='fill'),(case,),
        call_arities={'fill':1},callee_environment=environment)[0]
    assert row.status != 'passed'


def test_explicit_opaque_return_override_cannot_replace_concrete_computation():
    environment = c.Environment({'readWord':c.Leaf('lw v0,0(a0)\njr ra\nnop','synthetic')})
    case = d.TestCase('override',1,call_returns=(('readWord',0,999),))
    row = d.run_suite(caller(),caller(),(case,),call_arities={'readWord':1},
        return_registers=('v0',),callee_environment=environment)[0]
    assert row.target.return_values['v0'] == 7


@pytest.mark.grounded
def test_real_checksum_admission_and_byte_sum(repo_path):
    environment, report = c.load_binary_leaves(repo_path,['__osSumcalc','__osContRamRead'])
    assert '__osSumcalc' in environment.leaves, report
    assert '__osContRamRead' not in environment.leaves
    body = caller(call='__osSumcalc').replace('jal __osSumcalc','li a1,4\njal __osSumcalc')
    case = d.TestCase('actual-checksum',1)
    row = d.run_suite(body,body,(case,),call_arities={'__osSumcalc':2},
        return_registers=('v0',),callee_environment=environment)[0]
    assert row.status == 'passed' and row.target.return_values['v0'] == 7
    assert len(row.target.concrete_calls[0]['execution']['instruction_trace']) > 40


@pytest.mark.grounded
def test_mnemonics_cannot_hide_behind_unchanged_rom_annotations(repo_path,tmp_path):
    import yaml
    config = yaml.safe_load((repo_path/'snowboardkids.yaml').read_text())
    config['options']['target_path'] = str(repo_path/config['options']['target_path'])
    (tmp_path/'snowboardkids.yaml').write_text(yaml.safe_dump(config))
    (tmp_path/'symbol_addrs.txt').write_text((repo_path/'symbol_addrs.txt').read_text())
    (tmp_path/'asm').mkdir()
    body = (repo_path/'asm/matchings/ultra/io/contpfs/__osSumcalc.s').read_text()
    assert 'lbu' in body
    (tmp_path/'asm/__osSumcalc.s').write_text(body.replace('lbu','lb'))
    environment,report = c.load_binary_leaves(tmp_path,['__osSumcalc'])
    assert not environment.leaves
    assert 'do not reassemble to ROM' in report[0]['reason']


def test_explorer_retains_unsupported_callee_evidence_outside_selected_cases():
    body = caller().replace('sw t0,24(sp)','nop')
    environment = c.Environment({'readWord':c.Leaf('lw v0,0(a0)\njr ra\nnop','synthetic')})
    report = d.explore_coverage(body,(d.TestCase('uninitialized',1),),max_cases=4,
        call_arities={'readWord':1},callee_environment=environment)
    assert report.execution_obstructions
    assert 'uninitialized' in report.to_dict()['execution_obstructions'][0]['error']
