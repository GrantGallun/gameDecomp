from solver import frontend_repair as repair, sdk_intake
from solver import repair_context


def diagnostic(source, line, token, message):
    text = source.splitlines()[line-1]
    return f'candidate.c:{line}:{text.index(token)+1}: error: {message}\n {line} | {text}\n'


def test_literal_address_requires_target_and_exact_diagnostic(tmp_path):
    source = 'void f(void) {\n    load(0x1234, 7);\n}\n'
    d = diagnostic(source, 2, '0x', "incompatible integer to pointer conversion passing 'int' to parameter of type 'void *'")
    assert not repair.propose(tmp_path, source, 'f', d)['changes']
    r = repair.propose(tmp_path, source, 'f', d, big_endian_o32=True)
    assert 'load((void *)0x1234, 7)' in r['source']
    assert not repair.propose(tmp_path, source.replace('1234', '4567'), 'f', d, big_endian_o32=True)['changes']


def test_byte_cursor_preserves_address(tmp_path):
    source = 'void f(void) {\n    cursor = &object;\n}\n'
    d = diagnostic(source, 2, '&object', "incompatible pointer types assigning to 'u8 *' (aka 'unsigned char *') from 'Thing *'")
    assert 'cursor = (unsigned char *)(&object);' in repair.propose(tmp_path, source, 'f', d)['source']
    assert not repair.propose(tmp_path, source, 'f', d.replace("'u8 *'", "'u16 *'"))['changes']


def test_u64_call_packs_both_words_and_preserves_header(tmp_path):
    (tmp_path/'include').mkdir()
    (tmp_path/'include'/'math.h').write_text('unsigned int root(u64 value);\n')
    source = '#include "math.h"\nvoid f(void) {\n    x = root((low < old) + high, low);\n}\n'
    d = diagnostic(source, 3, 'low);', "too many arguments to function call, expected single argument 'value', have 2 arguments")
    r = repair.propose(tmp_path, source, 'f', d, big_endian_o32=True)
    assert 'root((((u64)(u32)((low < old) + high) << 32) | (u32)(low)))' in r['source']
    assert r['changes'][0]['kind'] == 'o32-u64-argument-pack'
    (tmp_path/'include'/'math.h').write_text('unsigned int root(u32 value);\n')
    assert not repair.propose(tmp_path, source, 'f', d, big_endian_o32=True)['changes']


def test_u64_side_effects_decline(tmp_path):
    (tmp_path/'include').mkdir()
    (tmp_path/'include'/'math.h').write_text('unsigned int root(u64 value);\n')
    for expression in ['next()', 'high++', 'high = 7']:
        source = '#include "math.h"\nvoid f(void) {\n    x = root('+expression+', low);\n}\n'
        d = diagnostic(source, 3, 'low', "too many arguments to function call, expected single argument 'value', have 2 arguments")
        assert not repair.propose(tmp_path, source, 'f', d, big_endian_o32=True)['changes']


def test_trap_is_not_hardware_impossibility():
    rows = [dict(pc=0, word='0007000d', opcode='break', operands='0x7')]
    r = sdk_intake.classify(rows)
    assert r['status'] == 'sdk_control_flow_or_relocation_unsupported'
    assert 'compiler-generated' in r['trap_policy']
    assert r['unsupported_opcode_counts'] == {'break': 1}


def test_elf_gate(tmp_path):
    p = tmp_path/'target.o'
    assert not repair.big_endian_o32(p)


def test_void_parameter_call_offset():
    source = 'void f(void *base) {\n    link(x, base + 12);\n}\n'
    rows = dict(repair_context.normalize(source, 'Unacceptable operand', 'f'))
    assert 'link(x, (void *)((unsigned char *)base + 12));' in rows['void-pointer-call-byte-offset']
    for other in ['base[0] + 12', 'base + offset', 'base + 12 * 4']:
        altered = source.replace('base + 12', other)
        assert 'void-pointer-call-byte-offset' not in dict(repair_context.normalize(altered, 'Unacceptable operand', 'f'))


def test_void_local_arrow_is_not_subtraction():
    source = 'void f(void) {\n    void *cursor;\n    cursor->unk10 = 1;\n    use(cursor + 4);\n}\n'
    rows = dict(repair_context.normalize(source, 'Unacceptable operand', 'f'))
    assert 'cursor->unk10' in rows['void-local-byte-arithmetic']
    assert '((unsigned char *)cursor)->' not in rows['void-local-byte-arithmetic']


def test_signed_wide_argument(tmp_path):
    (tmp_path/'include').mkdir()
    (tmp_path/'include'/'math.h').write_text('int root(s64 value);\n')
    source = '#include "math.h"\nvoid f(void) {\n    x = root(high, low);\n}\n'
    d = diagnostic(source, 3, 'low', "too many arguments to function call, expected single argument 'value', have 2 arguments")
    r = repair.propose(tmp_path, source, 'f', d, big_endian_o32=True)
    assert 'root((s64)(((u64)(u32)(high) << 32) | (u32)(low)))' in r['source']


def test_elf_flags(tmp_path):
    p = tmp_path/'target.o'
    data = bytearray(52)
    data[:6] = b'\x7fELF\x01\x02'
    data[18:20] = (8).to_bytes(2, 'big')
    data[36:40] = (0x1000).to_bytes(4, 'big')
    p.write_bytes(data)
    assert repair.big_endian_o32(p)
    data[5] = 1
    p.write_bytes(data)
    assert not repair.big_endian_o32(p)
