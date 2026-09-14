import copy
import hashlib
from solver import indexed_address_repair as repair


def test_measured_pointer_table_load():
    source='''extern s32 table;
void f(void) {
    s8 *p;
    p = *(s32 *)((u8 *)&table + (index.value * 4));
}
'''
    asm='''f:
lui t0, %hi(index)
lh t0, %lo(index)(t0)
lui t1, %hi(table)
sll t2, t0, 2
addu t1, t1, t2
lw v0, %lo(table)(t1)
jr ra
nop
'''
    r=repair.propose(source,'f',asm,measurement(source))
    assert '*(s8 **)((u8 *)&table' in r['source']
    assert len(r['changes'])==1
    for a in [asm.replace('lw v0','lh v0'),asm.replace('t0, 2','t0, 3'),asm.replace('lh t0','lhu t0')]:
        assert not repair.propose(source,'f',a,measurement(source))['changes']
    for s in [source.replace('s8 *p','s8 p'),source.replace('void f(void)','void f(int index)'),
              source.replace('    s8 *p;','    s8 *p;\n    Index index;')]:
        assert not repair.propose(s,'f',asm,measurement(s))['changes']

SOURCE = '''extern s32 table[];
extern s16 angles;
void f(void) {
    consume((index.value * 0x10) + &table);
    use(*(&angles + (index.value * 0x10)));
}
'''
ASM = '''f:
lui t0, %hi(index)
lh t0, %lo(index)(t0)
lui t1, %hi(table)
addiu t1, t1, %lo(table)
sll t2, t0, 4
jal consume
addu a0, t2, t1
lui t0, %hi(index)
lh t0, %lo(index)(t0)
lui t1, %hi(angles)
sll t2, t0, 4
addu a1, t1, t2
lh a1, %lo(angles)(a1)
jr ra
nop
'''


def measurement(source=SOURCE):
    return {'kind': 'target-compiler-header-layouts',
        'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'global_declarations': [{'name': 'index', 'spelling': 'Index'}],
        'layouts': {'Index': [{'member': 'value', 'spelling': 's16', 'width': 2, 'offset': 0}]}}


def test_delay_slot_call_and_split_relocation_load():
    r = repair.propose(SOURCE, 'f', ASM, measurement())
    assert len(r['changes']) == 2
    assert '(void *)((u8 *)&table + (index.value * 0x10))' in r['source']
    assert '*(s16 *)((u8 *)&angles + (index.value * 0x10))' in r['source']
    assert {w['kind'] for w in repair.witnesses(ASM)} == {'call-address','scalar-load'}


def test_requires_signedness_width_offset_and_stride():
    for asm in [ASM.replace('lh t0', 'lhu t0'), ASM.replace('lh t0', 'lw t0'),
                ASM.replace('t0, 4', 't0, 3'), ASM.replace('%lo(index)', '%lo(other)')]:
        assert not repair.propose(SOURCE, 'f', asm, measurement())['changes']
    for key, value in [('spelling','u16'), ('width',4), ('offset',2), ('pointer',True)]:
        m = measurement()
        m['layouts']['Index'][0][key] = value
        assert not repair.propose(SOURCE, 'f', ASM, m)['changes']


def test_incomplete_address_and_call_clobbers_decline():
    for asm in [ASM.replace('%lo(table)', '%lo(other)').replace('%lo(angles)', '%lo(other)'),
                ASM.replace('sll t2, t0, 4', 'sll t2, t0, 4\njal clobber\nnop'),
                ASM.replace('addu a0, t2, t1', 'nop').replace('lh a1, %lo(angles)(a1)', 'nop')]:
        assert not repair.propose(SOURCE, 'f', asm, measurement())['changes']


def test_source_mutation_and_stale_or_ambiguous_measurement():
    source = SOURCE.replace('consume(', 'index.value = 7; consume(')
    assert not repair.propose(source, 'f', ASM, measurement(source))['changes']
    m = measurement()
    m['layouts']['Index'].append(copy.deepcopy(m['layouts']['Index'][0]))
    assert not repair.propose(SOURCE, 'f', ASM, m)['changes']
    assert not repair.propose(SOURCE+' ', 'f', ASM, measurement())['changes']


def test_other_functions_comments_and_table_load_type():
    tail = '\nvoid g(void) { use(*(&angles + (index.value * 0x10))); }\n/* index.value */'
    source = SOURCE.replace('extern s16 angles;', 'extern u16 angles;')+tail
    r = repair.propose(source, 'f', ASM, measurement(source))
    assert len(r['changes']) == 1
    assert r['source'].endswith(tail)
    assert '*(&angles + (index.value * 0x10))' in r['source']


def test_recovery_measures_fresh_source_and_skips_already_lowered(monkeypatch, tmp_path):
    from solver import type_constraints
    seen = []
    def measure(repo, ws, source, function, target):
        seen.append((source, target))
        return measurement(source)
    monkeypatch.setattr(type_constraints, 'measure', measure)
    r = repair.recover(tmp_path, tmp_path, SOURCE, 'f', ASM, 'build/f.o')
    assert len(r['changes']) == 2 and seen == [(SOURCE, 'build/f.o')]
    assert not repair.recover(tmp_path, tmp_path, r['source'], 'f', ASM, 'build/f.o')['changes']
    assert len(seen) == 1


def test_recovery_routes_measurement_failure(monkeypatch, tmp_path):
    from solver import type_constraints
    def fail(*args):
        raise ValueError('unsupported header')
    monkeypatch.setattr(type_constraints, 'measure', fail)
    r = repair.recover(tmp_path, tmp_path, SOURCE, 'f', ASM, 'build/f.o')
    assert r['source'] == SOURCE and not r['changes'] and r['declines']
