from solver import address_units


SOURCE = '''extern s32 *cursor;
void f(void) {
    s32 *tmp;
    tmp = cursor;
    cursor = tmp + 8;
    (*(void **)((u8 *)(tmp) + 4)) = 0x2001000;
}
'''
ASM = '''f:
lui t0, %hi(cursor)
lw t1, %lo(cursor)(t0)
addiu t2, t1, 8
sw t2, %lo(cursor)(t0)
la t3, D_2001000
sw t3, 4(t1)
jr ra
nop
'''


def test_binary_update_and_linker_backed_address():
    r = address_units.propose(SOURCE, 'f', ASM, absolute_symbols={'D_2001000': 0x2001000})
    assert len(r['changes']) == 2
    assert 'cursor = (s32 *)((u8 *)tmp + 8);' in r['source']
    assert '= (void *)0x2001000;' in r['source']


def test_declines_wrong_unresolved_or_extra_target_write():
    for asm in [ASM.replace('t1, 8', 't1, 32'),
                ASM.replace('addiu t2, t1, 8', 'lw t2, 0(a0)'),
                ASM.replace('jr ra', 'sw t1, %lo(cursor)(t0)\njr ra')]:
        r = address_units.propose(SOURCE, 'f', asm)
        assert not r['changes'] and r['declines']


def test_declines_mutated_alias_and_mixed_update_family():
    for source in [SOURCE.replace('cursor = tmp + 8;', 'tmp++; cursor = tmp + 8;'),
                   SOURCE.replace('tmp = cursor;', 'tmp = cursor; tmp = other;'),
                   SOURCE.replace('cursor = tmp + 8;', 'cursor = tmp + 8; cursor = 0;'),
                   SOURCE.replace('s32 *tmp;', 'u32 *tmp;')]:
        r = address_units.propose(source, 'f', ASM)
        assert not r['changes'] and r['declines']


def test_pointer_constant_requires_linker_and_binary():
    no_step = SOURCE.replace('cursor = tmp + 8;', '')
    for symbols, asm in [({}, ASM), ({'D_2001000': 0x2001001}, ASM),
                         ({'D_2001000': 0x2001000}, ASM.replace('la t3, D_2001000', 'li t3, 17'))]:
        r = address_units.propose(no_step, 'f', asm, absolute_symbols=symbols)
        assert not r['changes']
    r = address_units.propose(no_step, 'f', ASM.replace('la t3, D_2001000','li t3, 0x2001000'),
        absolute_symbols={'D_2001000': 0x2001000})
    assert len(r['changes']) == 1


def test_preserves_scalar_stores_other_functions_and_comments():
    tail = '\nvoid other(void) { cursor = tmp + 8; }\n/* cursor = tmp + 8; */'
    source = SOURCE.replace('void **', 's32 *')+tail
    r = address_units.propose(source, 'f', ASM, absolute_symbols={'D_2001000': 0x2001000})
    assert len(r['changes']) == 1
    assert r['source'].endswith(tail)
    assert '= (void *)0x2001000;' not in r['source']
