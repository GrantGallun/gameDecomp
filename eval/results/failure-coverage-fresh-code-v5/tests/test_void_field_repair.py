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
