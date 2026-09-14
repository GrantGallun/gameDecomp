from solver import stack_result_repair as r

SOURCE='''void output(s32 *out, s32 a, s32 b);
void f(void) {
 s32 sp20;
 s32 sp24;
 s32 sp28;
 s32 sp2C;
 s32 sp40;
 output(&sp40, sp28, sp2C);
 use(sp40, sp44, sp4C);
}
'''
CALLER='''f:
addiu sp, sp, -96
lw t0, 40(sp)
lw t1, 44(sp)
addiu a0, sp, 64
lw a2, 32(sp)
lw a3, 36(sp)
sw t0, 16(sp)
jal output
sw t1, 20(sp)
lw t0, 68(sp)
lw t1, 76(sp)
jr ra
addiu sp, sp, 96
'''
CALLEE='''output:
move a1, a2
lw t0, 16(sp)
lw t1, 20(sp)
sw a2, 0(a0)
sw a3, 4(a0)
sw t0, 8(a0)
sw t1, 12(a0)
jr ra
nop
'''
DIAG="undeclared identifier 'sp44'\nundeclared identifier 'sp4C'"


def test_storage_and_wide_operands_change_together():
    result=r.propose(SOURCE,'f',CALLER,DIAG,{'output':CALLEE})
    assert result['changes']
    assert 's32 sp40[4];' in result['source']
    assert 'void output(s32 *, s64, s64);' in result['source']
    assert 'use(sp40[0], sp40[1], sp40[3])' in result['source']
    assert result['changes'][0]['words']==['sp20','sp24','sp28','sp2C']


def test_incomplete_arguments_or_result_extent_decline():
    for caller,callee in [(CALLER.replace('lw a2','lh a2'),CALLEE),
                          (CALLER,CALLEE.replace('12(a0)','16(a0)')),
                          (CALLER,CALLEE.replace('move a1, a2','sw a1, 0(a0)'))]:
        assert not r.propose(SOURCE,'f',caller,DIAG,{'output':callee})['changes']
    assert not r.propose(SOURCE+'\nvoid other(void) { output(&x,a,b); }','f',CALLER,DIAG,{'output':CALLEE})['changes']
