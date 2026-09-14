from solver import void_field_repair as r

SOURCE='void f(void *a) {\n    a->unkBC = 7;\n    use(a->unkEC);\n}\n'
ASM='f:\nsw t0, 0xbc(a0)\nlhu a1, 0xec(a0)\njr ra\nnop\n'


def diagnostic(source=SOURCE):
    rows=[]
    for n,line in enumerate(source.splitlines(),1):
        if '->unk' in line:
            rows.append(f"candidate.c:{n}:{line.index('->')+1}: error: member reference base type 'void' is not a structure or union\n {n} | {line}\n")
    return ''.join(rows)


def test_store_width_and_load_signedness():
    result=r.propose(SOURCE,'f',ASM,diagnostic())
    assert len(result['changes'])==2
    assert '(*(s32 *)((unsigned char *)a + 0xBC)) = 7' in result['source']
    assert 'use((*(u16 *)((unsigned char *)a + 0xEC)))' in result['source']


def test_missing_ambiguous_stack_or_stale_evidence():
    for asm in ['',ASM.replace('(a0)','(sp)'),ASM.replace('0xbc','0xbe').replace('0xec','0xee'),
                ASM.replace('jr ra','lb t0, 0xbc(a0)\nlh t0, 0xec(a0)\njr ra')]:
        assert not r.propose(SOURCE,'f',asm,diagnostic())['changes']
    assert not r.propose(SOURCE.replace(' = 7',' = 9'),'f',ASM,diagnostic())['source'].count('(*(s32 *)')


def test_nonvoid_or_shadowed_base_declines():
    for s in [SOURCE.replace('void *a','Actor *a'),SOURCE.replace('    a->','    void *a;\n    a->')]:
        assert not r.propose(s,'f',ASM,diagnostic(s))['changes']


def test_parameter_root_excludes_unrelated_offset_aliases():
    asm=ASM.replace('jr ra','lb t0, 0xbc(a1)\nlh t0, 0xec(a1)\njr ra')
    assert len(r.propose(SOURCE,'f',asm,diagnostic())['changes'])==2


def test_later_parameter_requires_known_single_word_prefix():
    source=SOURCE.replace('void *a','void *ctx, s32 count, u32 flags, void *a')
    asm=ASM.replace('(a0)','(a3)')
    assert len(r.propose(source,'f',asm,diagnostic(source))['changes'])==2
    for typ in ['s64','f32','SomeTypedef','struct Value']:
        bad=source.replace('s32 count',typ+' count')
        assert not r.propose(bad,'f',asm,diagnostic(bad))['changes']
    assert not r.propose(source,'f',ASM,diagnostic(source))['changes']


def test_parameter_relative_offset_survives_address_temporary():
    asm=ASM.replace('sw t0, 0xbc(a0)','addiu t1,a0,0xbc\nsw t0,0(t1)')
    assert len(r.propose(SOURCE,'f',asm,diagnostic())['changes'])==2


def test_fifth_word_pointer_from_entry_stack_slot():
    source=SOURCE.replace('void *a','void *ctx, u32 n, s32 x, s32 y, void *a')
    asm=ASM.replace('f:\n','f:\naddiu sp,sp,-32\nlw t2,48(sp)\n').replace('(a0)','(t2)')
    assert len(r.propose(source,'f',asm,diagnostic(source))['changes'])==2
    for wrong in (asm.replace('48(sp)','52(sp)'),
                  asm.replace('lw t2,48(sp)','sw zero,48(sp)\nlw t2,48(sp)')):
        assert not r.propose(source,'f',wrong,diagnostic(source))['changes']
    bad=source.replace('s32 x','s64 x')
    assert not r.propose(bad,'f',asm,diagnostic(bad))['changes']


def test_stack_argument_tracking_is_opt_in():
    from solver import dataflow
    asm='lw t2,16(sp)\nsw t0,4(t2)\njr ra\nnop'
    assert dataflow.analyse(asm).accesses[1].address!=dataflow.Value.address('param4',4)
    assert dataflow.analyse(asm,stack_parameter_words=1).accesses[1].address==dataflow.Value.address('param4',4)


def test_explicit_byte_cursor_chain_uses_parameter_relative_target_offset():
    source='''void f(void *a) {
    void *p;
    void *q;
    p = (void *)((unsigned char *)a + 8);
    q = (void *)(((unsigned char *)p) + 8);
    q->unk8 = 7;
}'''
    asm='addiu t2,a0,24\nsw t0,0(t2)\njr ra\nnop'
    assert len(r.propose(source,'f',asm,diagnostic(source))['changes'])==1
    assert not r.propose(source,'f',asm.replace('a0,24','a1,24'),diagnostic(source))['changes']
    bad=source.replace('q->unk8','p += 1;\n    q->unk8')
    assert not r.propose(bad,'f',asm,diagnostic(bad))['changes']
