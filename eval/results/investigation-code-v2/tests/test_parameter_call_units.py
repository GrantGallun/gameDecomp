import pytest

from solver import address_units, repair, workspace

ASM = '''glabel f
addiu a2,a0,0x44
jal consume
nop
jr ra
nop
'''


def source(expression, declarations='', statements=''):
    return 'void f(struct Actor *p) {\n'+declarations+'\n'+statements+'\nconsume(0, 0, '+expression+');\n}\n'


@pytest.mark.parametrize('expression', ['p + 0x44', '(void **)(p + 0x44)',
    '((void **)(p + 0x44))', '&p[0x44]', '0x44 + p'])
def test_repairs_compiling_outer_casts_and_index_forms(expression):
    result = address_units.parameter_call_views(source(expression), 'f', ASM, o32=True)
    assert len(result['changes']) == 1, result
    assert '(unsigned char *)p + 0x44' in result['source']
    assert not address_units.parameter_call_views(result['source'], 'f', ASM, o32=True)['changes']


def test_negative_displacement_and_alias():
    result = address_units.parameter_call_views(source('alias - 4',
        'struct Actor *alias;', 'alias = p;'), 'f', ASM.replace('0x44', '-4'), o32=True)
    assert result['changes'], result
    assert '(unsigned char *)alias - 0x4' in result['source']


@pytest.mark.parametrize('case', ['wrong_offset', 'missing_call', 'different_occurrences',
    'mutated', 'prefix_mutated', 'escaped', 'shadowed', 'non_o32', 'scalar', 'directive', 'byte'])
def test_declines_without_closed_pointer_and_target_witness(case):
    code, asm, o32 = source('(void **)(p + 0x44)'), ASM, True
    if case == 'wrong_offset': asm = asm.replace('0x44', '0x88')
    if case == 'missing_call': asm = asm.replace('consume', 'other')
    if case == 'different_occurrences': asm = asm.replace('jr ra', 'addiu a2,a0,12\njal consume\nnop\njr ra')
    if case == 'mutated': code = source('p + 0x44', statements='p++;')
    if case == 'prefix_mutated': code = source('p + 0x44', statements='++p;')
    if case == 'escaped': code = source('p + 0x44', statements='escape(&p);')
    if case == 'shadowed': code = source('p + 0x44', declarations='struct Actor *p;')
    if case == 'non_o32': o32 = False
    if case == 'scalar': code = code.replace('struct Actor *p', 'int p')
    if case == 'directive': code = source('p + 0x44', statements='#if X\n#endif')
    if case == 'byte': code = code.replace('struct Actor *p', 'u8 *p')
    result = address_units.parameter_call_views(code, 'f', asm, o32=o32)
    assert not result['changes'], result
    assert result['declines']


def test_numeric_ratio_is_not_a_type_fact():
    result = address_units.numeric_relations(0x44, 0xee0)
    assert result[1] == {'kind': 'candidate_multiple', 'factor': 0x38, 'matches_measured_size': False}
    assert address_units.numeric_relations(0x32, 0x36, base='sp')[0]['kind'] == 'stack_delta'


def test_repair_scheduler_offers_shared_pointer_repair(monkeypatch):
    monkeypatch.setattr(repair.rewrites, 'propose', lambda *args: [])
    state = repair._State(source('(void **)(p + 0x44)'), workspace.Attempt(True, 99.5, False, '', '', ''))
    tasks = repair._proposal_tasks([state], pointer_context=dict(function='f', assembly=ASM, o32=True))
    assert len(tasks) == 1
    assert '(unsigned char *)p + 0x44' in tasks[0][1](state.source)
