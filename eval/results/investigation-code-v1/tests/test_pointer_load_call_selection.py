from solver.frontend_repair import propose

SOURCE='''void f(void *root) {
    convert((void *)((unsigned char *)root + 8));
    convert((*(s32 *)((unsigned char *)root + 0x28)));
}'''
ASM='''addiu sp,sp,-32
sw ra,28(sp)
or s0,a0,zero
jal convert
addiu a0,s0,8
jal convert
lw a0,0x28(s0)
lw ra,28(sp)
addiu sp,sp,32
jr ra
nop
'''


def diagnostic():
    line=SOURCE.splitlines()[2]
    return (f"candidate.c:3:{line.index('(*(s32')+1}: error: incompatible integer to pointer conversion passing 's32' to parameter of type 'void *'\n"
            f' 3 | {line}\n')


def test_repeated_callee_selected_by_loaded_argument_identity(tmp_path):
    r=propose(tmp_path,SOURCE,'f',diagnostic(),big_endian_o32=True,target_assembly=ASM)
    assert 'convert((*(void **)((unsigned char *)root + 0x28)))' in r['source']
    assert len(r['pointer_load_calls'])==1


def test_wrong_or_ambiguous_loaded_argument_declines(tmp_path):
    for asm in (ASM.replace('0x28(s0)','0x24(s0)'),
                ASM.replace('addiu a0,s0,8','lw a0,0x28(s0)')):
        assert propose(tmp_path,SOURCE,'f',diagnostic(),big_endian_o32=True,target_assembly=asm)['source']==SOURCE
