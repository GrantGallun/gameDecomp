from solver import local_record_repair as r


SOURCE = '''typedef struct {
    char pad0[4];
    void *unk4;
    char pad1[8];
    void *unkC;
} R;
void f(R *p) { use(p->unkC); }
'''
ASM = 'lw a0,4(a1)\nlw a0,0xC(a1)'


def test_shrinks_padding_and_does_not_loop():
    result = r.propose(SOURCE, ASM)
    assert 'char pad8[0x4]' in result['source']
    assert not r.propose(result['source'], ASM)['changes']


def test_nested_halfword_offset():
    source = 'typedef struct {\n int unk16;\n} R;\nvoid f(R *p) {}'
    result = r.propose(source, 'lh a0,0x16(a1)')
    assert 'short unk16;' in result['source']
    assert 'pad0[0x16]' in result['source']


def test_missing_ambiguous_or_stack_evidence_declines():
    for asm in ('', 'lw a0,4(sp)\nlw a0,0xC(sp)', ASM+'\nlh a0,0xC(a1)'):
        assert not r.propose(SOURCE, asm)['changes']


def test_byvalue_and_padding_uses_decline():
    for extra in ('\nR value;', '\nvoid g(R *p) { use(p->pad1); }'):
        assert not r.propose(SOURCE+extra, ASM)['changes']


def test_semantic_fields_and_unknown_declarations_not_deleted():
    assert not r.propose(SOURCE.replace('unkC', 'next'), ASM)['changes']
    assert not r.propose(SOURCE.replace('char pad1[8];', 'UNKNOWN foo;'), ASM)['changes']


def test_repeated_padding_names_across_records_are_not_accesses():
    other = 'typedef struct {\n char pad0[8];\n int unkC;\n} S;\nvoid g(S *p) {}'
    result = r.propose(SOURCE+other, ASM)
    assert {c['record'] for c in result['changes']} == {'R', 'S'}
