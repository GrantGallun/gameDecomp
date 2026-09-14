from solver.m2c_byte_view import named_record_word_reads

SOURCE='''void f(void) {
    out = M2C_UNALIGNED32((s32) M2C_FIELD(&record, Record *, 0));
}'''
ASM='''lui t0,%hi(record)
addiu t0,t0,%lo(record)
lw at,0(t0)
'''


def test_named_load_reads_bytes_not_aggregate_cast():
    source,rows=named_record_word_reads(SOURCE,'f',ASM)
    assert 'M2C_' not in source
    assert '((u8 *)&record)[3]' in source
    assert rows[0]['target_loads']==[2]


def test_wrong_width_offset_identity_and_clobber_decline():
    for assembly in [ASM.replace('lw at','lh at'), ASM.replace('0(t0)','4(t0)'),
                     ASM.replace('record','another'),ASM.replace('lw at','li t0,0\nlw at')]:
        assert named_record_word_reads(SOURCE,'f',assembly)[0]==SOURCE


def test_shadow_and_bare_value_wrapper_are_not_reinterpreted():
    for source in [SOURCE.replace('void f(void)', 'void f(Record record)'),
                   SOURCE.replace('    out', '    Record record;\n    out'),
                   SOURCE.replace('(s32) M2C_FIELD(&record, Record *, 0)', 'record')]:
        assert named_record_word_reads(source,'f',ASM)[0]==source
