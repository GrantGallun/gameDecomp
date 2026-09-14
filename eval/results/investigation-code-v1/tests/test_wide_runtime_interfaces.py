from solver import wide_runtime_interfaces as w
import pytest


@pytest.mark.parametrize('assembly,typ,operator', [(a, t, o) for a, t, _, o in w.HELPERS])
def test_closed_helper_candidate_repairs_broken_word_parameters(assembly, typ, operator):
    source = '#include "common.h"\n\n' + typ + ' arbitrary(s64 x_unk0, ? x_unk4) { return x_unk0; }\nint other(void) { return 7; }\n'
    changed, report = w.reconstruct_helper(source, 'arbitrary', assembly,
                                          big_endian_o32=True, compiler_mips='-mips3 -32')
    assert f'{typ} arbitrary({typ} wide_lhs, {typ} wide_rhs)' in changed
    assert f'return wide_lhs {operator} wide_rhs;' in changed
    assert changed.startswith('#include "common.h"\n\n')
    assert changed.endswith('\nint other(void) { return 7; }\n')
    assert report['changes'] and report['assembly_sha256']


def test_remainder_signedness_is_instructions_not_function_name():
    unsigned = w.recognize('glabel misleading_signed_name\n' + w.UNSIGNED_REMAINDER)
    assert unsigned['return_type'] == 'u64'
    assert unsigned['operation'] == 'unsigned-word-pair-remainder'
    changed_trap = w.UNSIGNED_REMAINDER.replace('break 7', 'nop')
    assert w.recognize(changed_trap) is None
    changed_return = w.UNSIGNED_RSHIFT.replace('dsra32 v0,v0,0', 'dsrl32 v0,v0,0')
    assert w.recognize(changed_return) is None


@pytest.mark.parametrize('assembly,abi,isa', [
    (w.MULTIPLY, False, '-mips3 -32'), (w.MULTIPLY, True, '-mips3'),
    (w.MULTIPLY, True, '-mips2'),
    (w.MULTIPLY.replace('dmultu', 'dmult'), True, '-mips3 -32'),
    (w.DIVIDE.replace('break 6', 'nop'), True, '-mips3 -32'),
    (w.MULTIPLY + 'sw v0,0(a0)\n', True, '-mips3 -32'),
])
def test_closed_helper_candidate_declines_changed_stream_or_unproven_abi(assembly, abi, isa):
    source = 'u64 arbitrary(void) { return 0; }'
    changed, report = w.reconstruct_helper(source, 'arbitrary', assembly,
                                          big_endian_o32=abi, compiler_mips=isa)
    assert changed == source and not report['changes']


def test_complete_wide_runtime_streams():
    assert w.recognize(w.MULTIPLY)['parameters']==['u64','u64']
    assert w.recognize(w.DIVIDE)['parameters']==['s64','s64']
    renamed=w.DIVIDE.replace('.nonzero','.L123').replace('.result','.L456')
    assert w.recognize(renamed)['argument_words']==4
    decorated='nonmatching __ll_mul, 0x30\nglabel __ll_mul\n'+w.MULTIPLY+'endlabel __ll_mul\n'
    assert w.recognize(decorated)['parameters']==['u64','u64']


def test_changed_operation_word_order_return_or_trap_declines():
    for text in [w.MULTIPLY.replace('dmultu','dmult'),
                 w.MULTIPLY.replace('sw a0,0(sp)','sw a0,4(sp)'),
                 w.DIVIDE.replace('break 6','nop'),
                 w.DIVIDE.replace('dsra32 v0,v0,0','nop'),
                 w.DIVIDE.replace('bnez t7,.nonzero','bnez t7,.result')]:
        assert w.recognize(text) is None


def test_pairs_explicit_word_slots_and_projects_wide_return_high():
    source='''u64 multiply(u64,u64);
void f(void) {
    u64 value;
    value = multiply(/* u64+0x0 */ 0, /* u64+0x4 */ 2, /* u64+0x0 */ 0, /* u64+0x4 */ 3);
    value = multiply(/* u64+0x0 */ value, /* u64+0x4 */ (u32)value, /* u64+0x0 */ 0, /* u64+0x4 */ 4);
}'''
    r,changes=w.pair_arguments(source,[{'name':'multiply',**w.recognize(w.MULTIPLY)}])
    assert len(changes)==2 and 'value >> 32' in r
    assert '/* u64+' not in r and 'u64 multiply(u64,u64);' in r


def test_pairing_rejects_effects_or_missing_annotations():
    rows=[{'name':'divide',**w.recognize(w.DIVIDE)}]
    for source in ['divide(1,2,3,4);','divide(/* s64+0x0 */ side(), /* s64+0x4 */ 2, /* s64+0x0 */ 0, /* s64+0x4 */ 4);']:
        assert w.pair_arguments(source,rows)[0]==source
