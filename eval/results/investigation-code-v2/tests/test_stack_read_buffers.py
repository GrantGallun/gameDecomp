from solver.stack_read_buffers import propose

SOURCE='''void f(void) {
    s32 sp40;
    u8 sp20;
    fetch(0, &sp20);
    result = sp24 + sp20;
}'''
ASM='''addiu sp,sp,-80
jal fetch
addiu a1,sp,32
lhu v0,36(sp)
lw v1,32(sp)
sw v0,64(sp)
addiu sp,sp,80
jr ra
nop
'''


def test_shared_output_buffer_recovers_word_and_halfword():
    r=propose(SOURCE,'f',ASM)
    assert r['changes'][0]['extent']==32
    assert 'fetch(0, sp20.bytes)' in r['source']
    assert 'sp20.bytes[4]' in r['source']
    assert 'sp24' not in r['source']


def test_wrong_address_signed_load_and_alias_write_decline():
    for asm in [ASM.replace('a1,sp,32','a1,sp,40'),ASM.replace('lhu v0','lh v0')]:
        assert propose(SOURCE,'f',asm)['source']==SOURCE
    source=SOURCE.replace('result = sp24 + sp20','sp24 = sp20')
    assert propose(source,'f',ASM)['source']==source
