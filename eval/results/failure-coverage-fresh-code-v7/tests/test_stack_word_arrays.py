from solver.stack_word_arrays import propose

SOURCE = '''void f(void) {
    s32 sp80;
    u32 sp84;
    s32 i;
    s32 offset;
    i = 0;
    for (;;) {
        offset = i * 4;
        (*(s32 *)((u8 *)((sp + offset)) + 0x80)) = value;
        i += 1;
        if (!(i < 3)) break;
    }
    sp80 = sp84 + sp88;
}'''
ASM = '''addiu sp,sp,-256
or s1,zero,zero
loop:
sll a0,s1,2
addu t0,sp,a0
sw v0,0x80(t0)
addiu s1,s1,1
slti at,s1,3
bnez at,loop
nop
lw v0,0x88(sp)
addiu sp,sp,256
jr ra
nop
'''


def test_reconstructs_shared_words_preserving_declared_signed_views():
    r = propose(SOURCE, 'f', ASM)
    assert len(r['changes']) == 1
    assert 'union { s32 s[3]; u32 u[3]; } stack_words_80;' in r['source']
    assert 'stack_words_80.s[i] = value;' in r['source']
    assert 'stack_words_80.s[0] = stack_words_80.u[1] + stack_words_80.s[2];' in r['source']
    assert r['changes'][0]['aliases'][-1]['inferred']
    decorated = ASM.replace('loop', '.LoopUpper')
    import re
    decorated = re.sub(r'\b(sp|s1|a0|t0|v0|at|zero|ra)\b', r'$\1', decorated)
    decorated = 'nonmatching f, 64\n' + decorated + 'endlabel f\n'
    assert propose(SOURCE, 'f', decorated)['source'] == r['source']


def test_declines_wrong_stride_extent_and_alias_shape():
    for asm in (ASM.replace('s1,2', 's1,3'), ASM.replace('s1,3', 's1,4'),
                ASM.replace('0x80(t0)', '0x84(t0)'), ASM.replace('addu t0,sp,a0', 'addu t0,s0,a0')):
        assert propose(SOURCE, 'f', asm)['source'] == SOURCE
    for source in (SOURCE.replace('u32 sp84;', 's16 sp84;'),
                   SOURCE.replace('sp84 +', '&sp84 +'),
                   SOURCE.replace('i += 1;', 'i += 2;'),
                   SOURCE.replace('= value;', '= value; offset = 8; i = 2;')):
        assert propose(source, 'f', ASM)['source'] == source


def test_named_array_anchor_shares_storage_with_scalar_aliases():
    source = SOURCE.replace('    s32 sp80;\n', '').replace(
        '(*(s32 *)((u8 *)((sp + offset)) + 0x80)) = value;',
        'cursor = &(&sp80[0])[i];\n        *cursor = value;').replace('sp80 =', 'sp80[0] =')
    asm = ASM.replace('addu t0,sp,a0', 'addiu t1,sp,0x80\naddu t0,t1,a0').replace('0x80(t0)', '0(t0)')
    r = propose(source, 'f', asm)
    assert len(r['changes']) == 1
    assert '&(&stack_words_80.s[0])[i]' in r['source']
    assert 'stack_words_80.s[0] = stack_words_80.u[1]' in r['source']
    assert propose(source, 'f', asm.replace('sp,0x80', 'sp,0x84'))['source'] == source
