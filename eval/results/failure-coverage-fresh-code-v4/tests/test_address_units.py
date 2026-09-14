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


OBJECT = '''extern M2C_UNK layout;
void f(int i) {
    void *p;
    p = (i * 8) + ((i & 1) * 4) + &layout;
    use((*(s16 *)((u8 *)(p) + 2)));
}
'''
ADDRESS = 'f:\nlui t0, %hi(layout)\naddiu t0, t0, %lo(layout)\njr ra\nnop\n'


def test_address_only_incomplete_byte_view_keeps_index_expression():
    r = address_units.address_only_globals(OBJECT, 'f', ADDRESS, {'layout': 0x80001000})
    assert len(r['changes']) == 1
    assert 'extern u8 layout[];' in r['source']
    assert '(i * 8) + ((i & 1) * 4) + layout;' in r['source']
    assert r['changes'][0]['address_pair_instructions'] == [[0,1]]


def test_address_only_requires_linker_binary_and_closed_source_uses():
    for source, assembly, symbols, provided in [
        (OBJECT, ADDRESS, {}, ()),
        (OBJECT, ADDRESS.replace('%lo(layout)', '%lo(other)'), {'layout': 1}, ()),
        (OBJECT, ADDRESS, {'layout': 1}, ('layout',)),
        (OBJECT+'int g(void) { return layout; }', ADDRESS, {'layout': 1}, ()),
        (OBJECT.replace('void *p;', 's16 *p;'), ADDRESS, {'layout': 1}, ()),
        (OBJECT.replace('use((*(s16 *)((u8 *)(p) + 2)));', 'use(p);'), ADDRESS, {'layout': 1}, ()),
        (OBJECT.replace('p = (i * 8)', 'read(layout); p = (i * 8)'), ADDRESS, {'layout': 1}, ())]:
        r = address_units.address_only_globals(source, 'f', assembly, symbols, provided)
        assert not r['changes'] and r['source'] == source


def test_endpoint_view_requires_binary_comparison_and_plain_pointer_use():
    source='extern M2C_UNK end;\nvoid f(void) {\n    Camera *p;\n    if (p != &end) work();\n}\n'
    assembly='f:\nlui t0, %hi(end)\naddiu t0,t0,%lo(end)\nbne a0,t0,done\nnop\ndone:\njr ra\nnop'
    r=address_units.address_only_globals(source,'f',assembly,{'end':123})
    assert r['changes'] and 'extern u8 end[];' in r['source']
    assert 'p != (void *)end' in r['source']
    unnamed=address_units.address_only_globals(source,'f',assembly,{})
    assert unnamed['changes'][0]['symbol_address'] is None
    assert 'numeric address unresolved' in unnamed['changes'][0]['identity_authority']
    for s,a in [(source,assembly.replace('bne a0,t0,done','nop')),
                (source.replace('Camera *p;','s32 p;'),assembly),
                (source.replace('&end)', '&end + 1)'),assembly),
                (source.replace('p !=', 'obj.p !='),assembly),
                (source.replace('work();', 'use(&end);'),assembly)]:
        assert not address_units.address_only_globals(s,'f',a,{'end':123})['changes']


def test_address_pair_allows_scheduling_but_not_clobbers_or_calls():
    scheduled=ADDRESS.replace('addiu t0', 'li t2, 1\nnop\naddiu t0')
    assert address_units.address_only_globals(OBJECT,'f',scheduled,{'layout':123})['changes']
    for middle in ['li t0,1\n','jal other\nnop\n','nop\n'*16]:
        asm=ADDRESS.replace('addiu t0',middle+'addiu t0')
        assert not address_units.address_only_globals(OBJECT,'f',asm,{'layout':123})['changes']


def test_unsigned_endpoint_keeps_integer_comparison_and_requires_rhs_address():
    source = 'extern M2C_UNK end;\nvoid f(void) {\n u8 *p;\n if ((u32) p < (u32) &end) work();\n}\n'
    asm = 'f:\nlui t0,%hi(end)\n' + 'nop\n'*9 + 'addiu t0,t0,%lo(end)\nsltu t1,a0,t0\njr ra\nnop\n'
    report = address_units.address_only_globals(source, 'f', asm, {})
    assert 'extern u8 end[];' in report['source']
    assert '(u32) p < (u32) end' in report['source']
    assert report['changes'][0]['unsigned_comparison_instructions'] == [11]
    for candidate, binary in [
        (source.replace('(u32)', '(s32)'), asm),
        (source.replace(' < ', ' <= '), asm),
        (source.replace('&end)', '&end + 1)'), asm),
        (source.replace('u8 *p;', 'u32 p;'), asm),
        (source.replace('work();', 'read(end);'), asm),
        (source, asm.replace('sltu', 'slt')),
        (source, asm.replace('t1,a0,t0', 't1,t0,a0')),
        (source, asm.replace('sltu', 'li t0,0\nsltu')),
    ]:
        assert not address_units.address_only_globals(candidate, 'f', binary, {})['changes']
