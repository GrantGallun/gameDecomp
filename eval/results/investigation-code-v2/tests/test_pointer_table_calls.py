from solver.frontend_repair import propose

SOURCE='''extern s16 index;
extern s32 table;
void f(void) {
    draw(1, 2, *(&table + (index * 4)));
}'''
ASM='''lui t0,%hi(index)
lh t0,%lo(index)(t0)
sll t0,t0,2
lui a2,%hi(table)
addu a2,a2,t0
lw a2,%lo(table)(a2)
jal draw
nop
jr ra
nop
'''


def diagnostic():
    line=SOURCE.splitlines()[3]
    return f"candidate.c:4:{line.index('*(&')+1}: error: incompatible integer to pointer conversion passing 's32' to parameter of type 'Script *'\n 4 | {line}\n"


def test_target_pointer_argument_preserves_byte_stride(tmp_path):
    r=propose(tmp_path,SOURCE,'f',diagnostic(),big_endian_o32=True,target_assembly=ASM)
    assert '*(Script **)((u8 *)&table + (index * 4))' in r['source']


def test_clobber_index_width_or_target_callee_declines(tmp_path):
    for asm in [ASM.replace('lh t0','lhu t0'),ASM.replace('jal draw','jal other'),
                ASM.replace('jal draw','li a2,0\njal draw'),ASM.replace('jal draw\nnop','jal draw\nli a2,0')]:
        assert propose(tmp_path,SOURCE,'f',diagnostic(),big_endian_o32=True,target_assembly=asm)['source']==SOURCE
