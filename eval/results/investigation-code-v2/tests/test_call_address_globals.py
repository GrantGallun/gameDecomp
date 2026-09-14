from solver.call_address_globals import propose, witnesses

SOURCE='''extern M2C_UNK table;
void f(void) {
    transform(0, &table + 0x48, 0);
    transform(0, &table + (i * 0xC) + 0x18, 0);
}'''
ASM='''lui a1,%hi(table + 0x48)
addiu a1,a1,%lo(table + 0x48)
jal transform
nop
lui s4,%hi(table)
addiu s4,s4,%lo(table)
addiu s5,zero,12
multu s1,s5
mflo v0
addu a1,s4,v0
jal transform
addiu a1,a1,24
'''


def test_constant_and_indexed_call_addresses_preserve_bytes():
    r=propose(SOURCE,'f',ASM,{'table':0x80001000})
    assert 'extern u8 table[];' in r['source']
    assert '(void *)(table + (i * 0xC) + 0x18)' in r['source']


def test_mismatch_clobber_or_extra_use_declines():
    for asm in [ASM.replace('s5,zero,12','s5,zero,16'),ASM.replace('a1,a1,24','a1,a1,20'),
                ASM.replace('addu a1,s4,v0','li s4,0\naddu a1,s4,v0')]:
        assert propose(SOURCE,'f',asm,{'table':1})['source']==SOURCE
    assert propose(SOURCE,'f',ASM,{})['source']==SOURCE
    source=SOURCE.replace('    transform(0, &table + 0x48, 0);','    consume(table);\n    transform(0, &table + 0x48, 0);')
    assert propose(source,'f',ASM,{'table':1})['source']==source


def test_no_witness_reuse_or_stale_delay_slot_argument():
    source=SOURCE.replace('    transform(0, &table + 0x48, 0);','    transform(0, &table + 0x48, 0);\n    transform(0, &table + 0x48, 0);')
    assert propose(source,'f',ASM,{'table':1})['source']==source
    assembly='''lui a1,%hi(table)
jal prior
addiu a1,a1,%lo(table)
jal later
nop
'''
    assert not any(w['callee']=='later' for w in witnesses(assembly,'table'))
