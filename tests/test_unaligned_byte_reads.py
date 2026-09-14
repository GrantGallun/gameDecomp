from solver.m2c_byte_view import lower


def test_explicit_unaligned_wrapper_uses_byte_reads():
    source='s32 f(void *p) { return M2C_UNALIGNED32(M2C_FIELD(p, M2C_UNK *, -8)); }'
    asm='lwl t0, -8(a0)\nlwr t0, -5(a0)\n'
    r=lower(source,'f',target_assembly=asm)
    assert 'M2C_UNALIGNED32' not in r['source']
    assert 'M2C_UNK' not in r['source']
    assert '((u8 *)p)[-8]' in r['source'] and '<< 24' in r['source']
    assert len(r['unaligned_reads'])==1


def test_missing_pair_and_effectful_address_are_not_lowered():
    source='s32 f(void *p) { return M2C_UNALIGNED32(*p); }'
    assert 'M2C_UNALIGNED32' in lower(source,'f',target_assembly='lwl t0, 0(a0)\nlwr t0, 4(a0)')['source']
    source=source.replace('*p','*next()')
    assert 'M2C_UNALIGNED32' in lower(source,'f',target_assembly='lwl t0, 0(a0)\nlwr t0, 3(a0)')['source']
