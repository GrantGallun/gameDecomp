from solver.indexed_address_repair import affine_table_stores
from solver.frontend_repair import propose

ASM='''lui a0, %hi(index)
lbu t0, %lo(index)(a0)
sll t1, t0, 2
subu t1, t1, t0
sll t1, t1, 2
subu t1, t1, t0
sll t1, t1, 1
lui t2, %hi(table)
addiu t2, t2, %lo(table)
addu t3, t1, t2
lui at, %hi(dest)
sw t3, %lo(dest)(at)
'''


def test_affine_stride_and_frontend_projection(tmp_path):
    assert affine_table_stores(ASM,'index','table','dest',22)==[11]
    source='void f(void) {\n    dest = (index * 22) + &table;\n}\n'
    diag="candidate.c:2:10: error: incompatible pointer types assigning to 's16 *' from 'Rows (*)[3]'\n    2 |     dest = (index * 22) + &table;\n"
    result=propose(tmp_path,source,'f',diag,big_endian_o32=True,target_assembly=ASM)
    assert '(void *)((u8 *)&table + (index * 22))' in result['source']


def test_wrong_stride_provenance_width_or_clobber_decline():
    for asm in [ASM.replace('lbu','lb'),ASM.replace('sll t1, t1, 1','sll t1, t1, 2'),
                ASM.replace('addu t3', 'jal clobber\nnop\naddu t3'),ASM.replace('%lo(dest)','%lo(other)')]:
        assert not affine_table_stores(asm,'index','table','dest',22)
