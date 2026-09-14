from solver import format_buffer_repair as r

SOURCE='''void f(void) {
 M2C_UNK sp22;
 s8 sp20;
 sprintf(&sp20, "%2.2d", value);
 cursor = &sp20;
 consume((u32)&sp22, (u8)sp20);
}
'''
ASM='''f:
addiu sp, sp, -48
addiu s0, sp, 34
addiu a0, sp, 32
jal sprintf
nop
jr ra
addiu sp, sp, 48
'''


def test_buffer_extent_and_endpoints():
    result=r.propose(SOURCE,'f',ASM)
    assert result['changes']
    assert 's8 sp20[12];' in result['source']
    assert 'sprintf(sp20,' in result['source']
    assert '(sp20 + 2)' in result['source']
    assert '(u8)sp20[0]' in result['source']


def test_unknown_formats_missing_addresses_or_overlapping_slots_decline():
    for source,asm in [(SOURCE.replace('%2.2d','%s'),ASM),
                       (SOURCE,ASM.replace('sp, 34','sp, 36')),
                       (SOURCE,ASM.replace('jal sprintf','sw t0, 36(sp)\njal sprintf')),
                       (SOURCE.replace('%2.2d','%20d'),ASM)]:
        assert not r.propose(source,'f',asm)['changes']


def test_undeclared_byte_read_requires_matching_unsigned_load():
    source=SOURCE.replace('(u8)sp20','sp21')
    asm=ASM.replace('jr ra','lbu t0, 33(sp)\njr ra')
    result=r.propose(source,'f',asm)
    assert '((u8 *)sp20)[1]' in result['source']
    assert not r.propose(source,'f',ASM)['changes']
