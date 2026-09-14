from solver import frontend_repair as repair, sdk_intake
from solver import repair_context


def test_pointer_table_requires_binary_index_and_byte_stride(tmp_path):
    source = '''extern s32 table;
void f(Actor *arg0) {
    void *p;
    p = *(&table + ((*(u16 *)((u8 *)(arg0) + 0x10)) * 4));
}
'''
    asm = '''f:
lhu t0, 0x10(a0)
lui t1, %hi(table)
sll t2, t0, 2
addu t1, t1, t2
lw v0, %lo(table)(t1)
jr ra
nop
'''
    d = diagnostic(source, 4, '= ', "incompatible integer to pointer conversion assigning to 'void *' from 's32'")
    def run(s=source, a=asm, diag=d, abi=True):
        return repair.propose(tmp_path,s,'f',diag,big_endian_o32=abi,target_assembly=a)
    r = run()
    assert len(r['changes'])==1
    assert '*(void **)((u8 *)&table' in r['source']
    assert r['pointer_table_witnesses'][0]['stride']==4
    for a in [asm.replace('t0, 2','t0, 4'), asm.replace('lhu','lh'),
              asm.replace('0x10','0x12'),asm.replace('%lo(table)','%lo(other)'),
              asm.replace('0x10(a0)','0x10(a1)'), '']:
        assert not run(a=a)['changes']
    assert not run(abi=False)['changes']
    assert not run(s=source.replace('0x10','0x12'))['changes']
    shadow=source.replace('    void *p;', '    void *p;\n    Actor *arg0;')
    assert not run(s=shadow,diag=diagnostic(shadow,5,'= ',"integer to pointer conversion assigning to 'void *'"))['changes']


def diagnostic(source, line, token, message):
    text = source.splitlines()[line-1]
    return f'candidate.c:{line}:{text.index(token)+1}: error: {message}\n {line} | {text}\n'


def test_declared_byte_array_field_is_bounded(tmp_path):
    source='void f(void) {\n    u8 buffer[24];\n    buffer.unk17 = value;\n}\n'
    message="member reference base type 'u8[24]' is not a structure or union"
    d=diagnostic(source,3,'.unk',message)
    assert 'buffer[0x17] = value' in repair.propose(tmp_path,source,'f',d)['source']
    for s in [source.replace('[24]','[23]'),source.replace('u8 buffer','u16 buffer'),
              source.replace('void f(void)','void f(u8 *buffer)')]:
        assert not repair.propose(tmp_path,s,'f',d)['changes']
    assert not repair.propose(tmp_path,source.replace('value','other'),'f',d)['changes']


def test_header_bound_byte_cursor_argument(tmp_path):
    (tmp_path/'include').mkdir()
    header=tmp_path/'include'/'api.h'
    header.write_text('void consume(void *dst, Vec3i *value);\n')
    source='#include "api.h"\nvoid f(void) {\n    s8 *cursor;\n    consume(dst, cursor + 4);\n}\n'
    msg="incompatible pointer types passing 's8 *' (aka 'signed char *') to parameter of type 'Vec3i *'"
    d=diagnostic(source,4,'cursor +',msg)
    r=repair.propose(tmp_path,source,'f',d)
    assert 'consume(dst, (Vec3i *)(cursor + 4))' in r['source']
    for s in [source.replace('s8 *cursor','s16 *cursor'),source.replace('void f(void)','void f(s8 *cursor)')]:
        assert not repair.propose(tmp_path,s,'f',d)['changes']
    header.write_text('void consume(void *dst, Other *value);\n')
    assert not repair.propose(tmp_path,source,'f',d)['changes']
    assert not repair.propose(tmp_path,source.replace('cursor + 4','cursor + 8'),'f',d)['changes']


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
    annotated=source.replace('root((low', 'root(/* high word */ (low').replace(', low);', ', /* low word */ low);')
    ad=diagnostic(annotated,3,'low);',"too many arguments to function call, expected single argument 'value', have 2 arguments")
    assert repair.propose(tmp_path,annotated,'f',ad,big_endian_o32=True)['changes']
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


def test_integer_address_load_uses_declared_output_width(tmp_path):
    source = 'void f(u32 address, u32 *out) {\n    *out = *(address | (s32) 0xA0000000);\n}\n'
    diag = diagnostic(source, 2, '*(address', "indirection requires pointer operand ('u32' invalid)")
    result = repair.propose(tmp_path, source, 'f', diag, big_endian_o32=True)
    assert '*out = *(u32 *)(address | (s32) 0xA0000000);' in result['source']
    assert not repair.propose(tmp_path, source, 'f', diag)['changes']
    assert not repair.propose(tmp_path, source.replace('u32 *out','float *out'), 'f', diag, big_endian_o32=True)['changes']


def test_integer_address_declines_unknown_or_shadowed_base(tmp_path):
    source = 'void f(u32 address, u32 *out) {\n    u32 address;\n    *out = *(address | (s32) 0xA0000000);\n}\n'
    diag = diagnostic(source, 3, '(address', 'indirection requires pointer operand')
    assert not repair.propose(tmp_path, source, 'f', diag, big_endian_o32=True)['changes']


def test_incomplete_pointer_offset_preserves_public_type(tmp_path):
    source='void f(Opaque *base) {\n Matrix *dest;\n dest = base + 0x34;\n}\n'
    diag=diagnostic(source,3,'base',"arithmetic on a pointer to an incomplete type 'Opaque'")
    result=repair.propose(tmp_path,source,'f',diag,big_endian_o32=True)
    assert 'dest = (void *)((unsigned char *)base + 0x34)' in result['source']
    assert result['source'].startswith('void f(Opaque *base)')
    assert not repair.propose(tmp_path,source,'f',diag)['changes']


def test_complete_pointer_conversion_requires_binary_byte_offset(tmp_path):
    source='void f(Actor *base) {\n Matrix *dest;\n dest = base + 0x34;\n}\n'
    diag=diagnostic(source,3,'=',"incompatible pointer types assigning to 'Matrix *' from 'Actor *'")
    assert not repair.propose(tmp_path,source,'f',diag,big_endian_o32=True)['changes']
    for asm,expected in [('addiu a1,a0,0x34',True),('addiu a1,a0,0x38',False),('addiu a1,a2,0x34',False)]:
        result=repair.propose(tmp_path,source,'f',diag,big_endian_o32=True,target_assembly=asm)
        assert bool(result['changes'])==expected


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
