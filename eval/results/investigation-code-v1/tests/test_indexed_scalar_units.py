from solver.address_units import indexed_scalar_reads, propose

SOURCE='''extern s32 table;
int f(P *p) { return *(&table + (p->index * 4)); }'''
ASM='''lui t0, %hi(table)
sll t9, t8, 2
addu t0, t0, t9
lw t0, %lo(table)(t0)
'''


def test_explicit_byte_scale_word_read():
    candidate,changes=indexed_scalar_reads(SOURCE,'f',ASM)
    assert '(*(s32 *)((u8 *)&table + (p->index * 4)))' in candidate
    assert changes[0]['byte_scale']==4
    assert indexed_scalar_reads(candidate,'f',ASM)[0]==candidate
    assert propose(SOURCE,'f',ASM)['source']==candidate


def test_wrong_scale_width_base_or_extra_uses_decline():
    for bad in [ASM.replace('t8, 2','t8, 4'),ASM.replace('lw t0','lbu t0'),
                ASM.replace('%lo(table)(t0)','%lo(table)(t1)'),
                ASM.replace('addu t0, t0, t9','addu t0, t0, t8')]:
        assert indexed_scalar_reads(SOURCE,'f',bad)[0]==SOURCE
    bad=SOURCE.replace('return','consume(table); return')
    assert indexed_scalar_reads(bad,'f',ASM)[0]==bad
