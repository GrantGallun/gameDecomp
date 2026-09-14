import pytest
from solver.m2c_byte_view import lower

SOURCE='''void f(void *p) {
    M2C_FIELD(p, void *(**)(s16 *, s16 *, s32, Acmd *, Acmd *), 4)(a,b,c,d,e);
}'''
ASM='''lw t9, 4(t1)
move a0, t1
jalr t9
nop
'''


def test_callback_field_lowering_preserves_signature():
    r=lower(SOURCE,'f',known_types=('Acmd',),target_assembly=ASM)
    assert '(*(void *(**)(s16 *, s16 *, s32, Acmd *, Acmd *))((u8 *)(p) + 4))' in r['source']
    assert r['hypotheses'][0]['kind']=='callback-pointer-field'


def test_callback_requires_known_types_and_live_indirect_target():
    for asm in ['',ASM.replace('4(t1)','8(t1)'),ASM.replace('move a0, t1','move t9, t1')]:
        with pytest.raises(ValueError):lower(SOURCE,'f',known_types=('Acmd',),target_assembly=asm)
    with pytest.raises(ValueError):lower(SOURCE,'f',target_assembly=ASM)
