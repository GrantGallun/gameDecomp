from solver.m2c_copy import destination_storage, source_cursor, feeding_cursor, indexed_stack_read, unaligned_loop_layout

ASM='''addiu t6, sp, 44
addiu t9, t7, 36
loop:
lwl at, 0(t7)
lwr at, 3(t7)
addiu t7, t7, 12
addiu t6, t6, 12
sw at, -12(t6)
lwl at, -8(t7)
lwr at, -5(t7)
sw at, -8(t6)
lwl at, -4(t7)
lwr at, -1(t7)
bne t7, t9, loop
sw at, -4(t6)
lwl at, 0(t7)
lwr at, 3(t7)
sw at, 0(t6)
'''


def test_copy_extent_and_stack_destination():
    r=unaligned_loop_layout(ASM)['copies']
    assert len(r)==1 and r[0]['total_bytes']==40 and r[0]['stack_offset']==44


def test_incomplete_tail_bad_step_register_or_branch_rejected():
    for bad in [ASM.replace('t7, 12','t7, 4'),ASM.replace('sp, 44','sp, 45'),
                ASM.replace('t7, 36','t7, 35'),ASM.replace('t9, loop','t9, missing'),
                ASM.replace('sw at, 0(t6)','sw at, 4(t6)'),ASM.replace('lwr at, 3(t7)','lwr v0, 3(t7)')]:
        if bad!=ASM:assert not unaligned_loop_layout(bad)['copies']


SOURCE='''void f(void) {
    M2C_UNK sp2C;
    cursor = &sp2C;
    status = sp2E;
    crc = sp52;
    consume(&sp2C + 6);
}'''
LOADS=ASM+'lbu v0, 46(sp)\nlbu v1, 82(sp)\n'


def test_destination_storage_and_witnessed_byte_aliases():
    candidate,report=destination_storage(SOURCE,'f',LOADS)
    assert 'u32 sp2C[10];' in candidate
    assert 'status = ((u8 *)sp2C)[2];' in candidate
    assert 'crc = ((u8 *)sp2C)[38];' in candidate
    assert 'consume(((u8 *)sp2C) + 6);' in candidate
    assert report['changes'][0]['bytes']==40
    assert destination_storage(candidate,'f',LOADS)[0]==candidate


def test_destination_storage_declines_unwitnessed_or_mutated_aliases():
    assert destination_storage(SOURCE,'f',ASM)[0]==SOURCE
    for bad in [SOURCE.replace('status = sp2E;',statement) for statement in
                ['++sp2E;', '--sp2E;', 'sp2E |= 1;', 'sp2E <<= 1;',
                 'sp2E++;', 'consume(&sp2E);', 'u8 sp2E;', 'status = sp2C;']]:
        assert destination_storage(bad,'f',LOADS)[0]==bad


def test_destination_storage_declines_parameter_alias():
    bad=SOURCE.replace('f(void)','f(u8 sp2E)')
    assert destination_storage(bad,'f',LOADS)[0]==bad


CURSOR='''void f(void) {
    M2C_UNK sp2C;
    M2C_UNK *dst;
    dst = &sp2C;
    do {
        dst += 0xC;
        M2C_FIELD(dst, s32 *, -0xC) = a;
        M2C_FIELD(dst, s32 *, -8) = b;
        M2C_FIELD(dst, s32 *, -4) = c;
    } while (more);
    M2C_FIELD(dst, s32 *, 0) = d;
}'''


def test_closed_destination_cursor_becomes_byte_pointer():
    candidate,report=destination_storage(CURSOR,'f',ASM)
    assert 'u8 *dst;' in candidate
    assert report['changes'][0]['byte_cursors']==['dst']


def test_destination_cursor_escape_wrong_stride_and_offset_decline():
    for bad in [CURSOR.replace('dst += 0xC','dst += 4'),
                CURSOR.replace('s32 *, -8','s32 *, -7'),
                CURSOR.replace('} while (more);','} while (more);\n    escape(dst);')]:
        candidate,report=destination_storage(bad,'f',ASM)
        assert 'M2C_UNK *dst;' in candidate
        assert report['changes'][0]['byte_cursors']==[]


READ_CURSOR='''void f(void) {
    union _anonymous *src;
    u32 *limit;
    src = parent;
    limit = &src->words[9];
    do {
        a = M2C_UNALIGNED32(*src);
        src += 0xC;
        b = M2C_UNALIGNED32(M2C_FIELD(src, M2C_UNK *, -8));
        c = M2C_UNALIGNED32(M2C_FIELD(src, M2C_UNK *, -4));
    } while (src != limit);
    d = M2C_UNALIGNED32(M2C_FIELD(src, M2C_UNK *, 0));
}'''


def test_source_cursor_uses_target_byte_endpoint():
    candidate,report=source_cursor(READ_CURSOR,'f',ASM)
    assert 'u8 *src;' in candidate and 'u8 *limit;' in candidate
    assert 'limit = src + 36;' in candidate
    assert 'src = (u8 *)parent;' in candidate
    assert report['changes'][0]['bytes']==36
    assert source_cursor(candidate,'f',ASM)[0]==candidate


def test_source_cursor_declines_escape_offset_and_unwitnessed_extent():
    for bad in [READ_CURSOR.replace('words[9]','words[8]'),
                READ_CURSOR.replace('src += 0xC','src += 4'),
                READ_CURSOR.replace('M2C_UNK *, -8','M2C_UNK *, -7'),
                READ_CURSOR.replace('} while','escape(src);\n    } while'),
                READ_CURSOR.replace('} while','escape(limit);\n    } while')]:
        assert source_cursor(bad,'f',ASM)[0]==bad
    assert source_cursor(READ_CURSOR,'f','')[0]==READ_CURSOR


FEED='''void f(void) {
    union _anonymous *sp54;
    sp54 = &packet;
    sp54 += 1;
    src = (u8 *)sp54;
}'''
FEED_ASM='''loop:
lw t1, 84(sp)
addiu t4, t3, 1
slt at, t4, t5
addiu t2, t1, 1
sw t4, 88(sp)
bnez at, loop
sw t2, 84(sp)
'''


def test_feeding_cursor_requires_stack_byte_stride():
    candidate,report=feeding_cursor(FEED,'f',FEED_ASM,['src'])
    assert 'u8 *sp54;' in candidate and 'sp54 = (u8 *)&packet;' in candidate
    assert report['changes'][0]['stack_offset']==84
    assert feeding_cursor(FEED,'f',FEED_ASM,[])[0]==FEED
    for bad in [FEED_ASM.replace('t2, t1, 1','t2, t1, 4'),
                FEED_ASM.replace('sw t2, 84','sw t2, 88'),
                FEED_ASM.replace('slt at,','slt t1,'),
                FEED_ASM.replace('at, loop','at, missing')]:
        assert feeding_cursor(FEED,'f',bad,['src'])[0]==FEED


def test_feeding_cursor_rejects_escape_and_shadow():
    for bad in [FEED.replace('sp54 += 1;','sp54 += 1; escape(sp54);'),
                FEED.replace('f(void)','f(void *sp54)')]:
        assert feeding_cursor(bad,'f',FEED_ASM,['src'])[0]==bad


def test_feeding_cursor_schedule_independent_def_use():
    reordered='''loop:
lw t1, 84(sp)
lw t6, 80(sp)
addiu t4, t3, 1
addiu t2, t1, 1
sw t4, 88(sp)
sw t2, 84(sp)
lw t7, 8(t6)
slt at, t4, t7
bnez at, loop
nop
'''
    assert 'u8 *sp54;' in feeding_cursor(FEED,'f',reordered,['src'])[0]
    for bad in [reordered.replace('sw t2, 84','sw t1, 84'),
                reordered.replace('addiu t2, t1, 1','lw t1, 80(sp)\naddiu t2, t1, 1'),
                reordered.replace('sw t2, 84(sp)','sw t2, 84(sp)\nsb zero, 85(sp)'),
                reordered.replace('lw t7, 8(t6)','jal unknown'),
                reordered.replace('lw t7, 8(t6)','lw sp, 8(t6)')]:
        assert feeding_cursor(FEED,'f',bad,['src'])[0]==FEED


INDEX_ASM='''sw zero, 88(sp)
loop:
lw t9, 88(sp)
lw t6, 108(sp)
addu t7, sp, t9
lbu t7, 50(t7)
sb t7, 0(t6)
lw t2, 88(sp)
lw t0, 108(sp)
addiu t3, t2, 1
slti at, t3, 32
addiu t1, t0, 1
sw t3, 88(sp)
bnez at, loop
sw t1, 108(sp)
'''
INDEX_SOURCE='''void f(void) {
    u32 sp2C[10];
    sp58 = 0;
    do {
        *buffer = M2C_FIELD((sp + sp58), u8 *, 0x32);
        temp = sp58 + 1;
        sp58 = temp;
        buffer += 1;
    } while (temp < 0x20);
}'''
STORAGE=[{'root':'sp2C','bytes':40,'copy':{'stack_offset':44}}]


def test_indexed_stack_read_bounded_reconstruction():
    candidate,report=indexed_stack_read(INDEX_SOURCE,'f',INDEX_ASM,STORAGE)
    assert '*buffer = ((u8 *)sp2C)[6 + sp58];' in candidate
    assert report['changes'][0]['bound']==32


def test_indexed_stack_read_rejects_bounds_stride_and_wrong_index():
    for bad in [INDEX_ASM.replace('t3, 32','t3, 40'),
                INDEX_ASM.replace('t3, t2, 1','t3, t2, 2'),
                INDEX_ASM.replace('sw zero','sw t0'),
                INDEX_ASM.replace('addu t7, sp, t9','addu t7, sp, t6')]:
        assert indexed_stack_read(INDEX_SOURCE,'f',bad,STORAGE)[0]==INDEX_SOURCE
    bad=INDEX_SOURCE.replace('temp < 0x20','temp < 0x21')
    assert indexed_stack_read(bad,'f',INDEX_ASM,STORAGE)[0]==bad
    assert indexed_stack_read(INDEX_SOURCE,'f',INDEX_ASM,[{**STORAGE[0],'bytes':36}])[0]==INDEX_SOURCE
