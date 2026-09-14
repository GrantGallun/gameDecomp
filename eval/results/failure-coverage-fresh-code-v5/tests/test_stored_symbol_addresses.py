from solver import address_units


SOURCE='''extern M2C_UNK asset;
void f(void) {
    (*(void **)((u8 *)(cursor) + 4)) = &asset;
}
'''
ASM='''lui t0,%hi(asset)
addiu t0,t0,%lo(asset)
sw t0,4(a0)
jr ra
nop'''


def test_address_store_needs_no_element_type_or_extent():
    result=address_units.address_only_globals(SOURCE,'f',ASM,{})
    assert 'extern u8 asset[];' in result['source']
    assert '= asset;' in result['source']
    assert result['changes'][0]['stored_address_instructions']==[2]


def test_unknown_value_and_nonclosed_use_decline():
    for asm in (ASM.replace('sw t0','sw t1'),ASM.replace('addiu t0,t0,%lo(asset)','nop')):
        assert not address_units.address_only_globals(SOURCE,'f',asm,{})['changes']
    assert not address_units.address_only_globals(SOURCE.replace('= &asset;', '= &asset; use(asset);'),'f',ASM,{})['changes']
    assert not address_units.address_only_globals(SOURCE,'f',ASM,{},('asset',))['changes']
