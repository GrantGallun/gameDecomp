from solver.m2c_byte_view import lower

SOURCE='''void f(void) {
    void *base;
    M2C_UNK *dst;
    M2C_UNK *src;
    M2C_UNK word;
    dst = base + 4;
    src = base + 0;
    word = M2C_UNALIGNED32(*src);
    src -= 4;
    *dst = M2C_UNALIGNED32(word);
    dst -= 4;
}'''
ASM='''lwl at,0(a1)
lwr at,3(a1)
addiu a1,a1,-4
swl at,0(a0)
swr at,3(a0)
addiu a0,a0,-4
'''


def test_copy_cursors_keep_byte_stride_and_read_before_step():
    r=lower(SOURCE,'f',target_assembly=ASM)
    assert 'M2C_UNK' not in r['source'] and 'M2C_UNALIGNED32' not in r['source']
    assert 'u8 *dst;' in r['source'] and 'u8 *src;' in r['source']
    assert 'u32 word;' in r['source']
    assert 'src = (u8 *)base + 0;' in r['source']
    assert r['source'].index('word = (s32)') < r['source'].index('src -= 4')
    assert len(r['copy_cursors'])==2 and len(r['word_temporaries'])==1


def test_extra_uses_and_unwitnessed_stride_remain_unknown():
    for addition in ['    use(src);\n','    word += 1;\n']:
        r=lower(SOURCE.replace('    dst -= 4;',addition+'    dst -= 4;'),'f',target_assembly=ASM)
        assert 'M2C_UNK' in r['source']
    r=lower(SOURCE,'f',target_assembly=ASM.replace(',-4',',-8'))
    assert not r['copy_cursors']


def test_typed_destination_is_not_reinterpreted_as_unknown_word():
    r=lower(SOURCE.replace('M2C_UNK *dst;', 'u16 *dst;'),'f',target_assembly=ASM)
    assert not r['unaligned_stores']
