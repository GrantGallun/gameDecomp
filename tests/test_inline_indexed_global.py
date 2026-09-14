from solver.address_units import address_only_globals

SOURCE='''extern M2C_UNK object;
void f(P *p) {
    (*(s32 *)((u8 *)((&object + (p->index * 0xB0))) + 0x94)) = p->x;
}'''
ASM='''lui v0,%hi(object)
addiu v0,v0,%lo(object)
addu t5,v0,t4
sw t3,0x94(t5)
'''


def test_inline_store_uses_named_global_byte_address():
    r=address_only_globals(SOURCE,'f',ASM,{'object':0x80001000})
    assert 'extern u8 object[];' in r['source']
    assert '(object + (p->index' in r['source']
    assert r['changes'][0]['inline_indexed_store_instructions']==[3]


def test_inline_store_requires_width_offset_live_symbol_and_identity():
    for bad in [ASM.replace('sw t3','sh t3'),ASM.replace('0x94(t5)','0x98(t5)'),
                ASM.replace('addu t5','li v0,0\naddu t5'),ASM.replace('addu t5,v0,t4','addu t5,t1,t4')]:
        assert address_only_globals(SOURCE,'f',bad,{'object':0x80001000})['source']==SOURCE
    assert address_only_globals(SOURCE,'f',ASM,{})['source']==SOURCE
