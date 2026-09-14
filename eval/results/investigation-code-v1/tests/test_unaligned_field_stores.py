from solver.m2c_byte_view import unaligned32_field_stores, lower

SOURCE = '''void f(void *dst, void *src) {
    M2C_FIELD(dst, M2C_UNK *, 0x10) = M2C_UNALIGNED32(*src);
}'''
ASM = '''lwl at,0(a1)
lwr at,3(a1)
swl at,16(a0)
swr at,19(a0)
'''


def test_word_is_read_once_before_four_byte_stores():
    result = lower(SOURCE, 'f', target_assembly=ASM)
    code = result['source']
    assert 'M2C_' not in code
    assert 'u32 _m2c_store_0_word' in code
    assert code.count('_m2c_store_0_dst[') == 4
    assert result['unaligned_stores'][0]['target_store_pairs'] == [[2, 3]]
    assert code.index('((u8 *)src)[3]') < code.index('_m2c_store_0_dst[0]')


def test_missing_wrong_offset_or_clobbered_pair_declines():
    for assembly in ['', ASM.replace('19(a0)', '20(a0)'),
                     ASM.replace('swr', 'li at,0\nswr'),
                     ASM.replace('swr', 'li a0,0\nswr'),
                     ASM.replace('swr', 'jal other\nnop\nswr')]:
        assert unaligned32_field_stores(SOURCE,'f',assembly)[0] == SOURCE


def test_nonadjacent_independent_instruction_and_effectful_base():
    assembly = ASM.replace('swr', 'li t0,0\nswr')
    assert unaligned32_field_stores(SOURCE,'f',assembly)[1]
    for base in ['get_dst()', 'dst++', '*ptr']:
        source = SOURCE.replace('M2C_FIELD(dst,', 'M2C_FIELD('+base+',')
        assert unaligned32_field_stores(source,'f',ASM)[0] == source
