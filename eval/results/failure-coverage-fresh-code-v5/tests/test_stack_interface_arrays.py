from solver.stack_interface_arrays import propose

SOURCE = '''void f(void) {
    s16 sp40;
    s16 sp42;
    make(&sp40);
    sp42 = 7;
    result = sp44;
}'''
ASM = '''addiu sp,sp,-128
jal make
addiu a0,sp,0x40
sh v0,0x42(sp)
lh v0,0x44(sp)
addiu sp,sp,128
jr ra
nop
'''
TYPES={'Matrix':('s16',3)}
DECLS={'make':['void make(Matrix out);']}


def test_array_call_contract_connects_missing_and_declared_aliases():
    r=propose(SOURCE,'f',ASM,TYPES,DECLS)
    assert len(r['changes'])==1
    assert 'Matrix sp40;' in r['source']
    assert 'make(sp40)' in r['source']
    assert 'sp40[1] = 7;' in r['source']
    assert 'result = sp40[2];' in r['source']


def test_wrong_call_address_type_or_unwitnessed_alias_declines():
    for asm in (ASM.replace('sp,0x40','sp,0x48'), ASM.replace('lh v0','lb v0')):
        assert propose(SOURCE,'f',asm,TYPES,DECLS)['source']==SOURCE
    for source in (SOURCE.replace('s16 sp42','s32 sp42'),SOURCE.replace('result = sp44','result = &sp44')):
        assert propose(source,'f',ASM,TYPES,DECLS)['source']==source
    assert propose(SOURCE,'f',ASM,TYPES,{'make':['void make(s16 *out);']})['source']==SOURCE
    assert propose(SOURCE,'f',ASM,TYPES,{'make':['void make(Matrix *out);']})['source']==SOURCE
