"""Admission must have executable witnesses, not just a permissive allowlist."""
import pytest
from solver import callee_execution as c, mips_differential as d


OPERANDS = {
    'nop':'', 'move':'v0,a1', 'li':'v0,7', 'lui':'v0,1',
    'addiu':'v0,a1,1', 'addu':'v0,a1,a2', 'subu':'v0,a1,a2',
    'neg':'v0,a1', 'negu':'v0,a1', 'not':'v0,a1',
    **{op:'v0,a1,a2' for op in ('and','or','xor','nor','slt','sltu')},
    **{op:'v0,a1,1' for op in ('andi','ori','xori','sll','srl','sra','slti','sltiu')},
    **{op:'v0,a1,a2' for op in ('sllv','srlv','srav')},
    **{op:'a1,a2' for op in ('mult','multu','div','divu')},
    'mflo':'v0','mfhi':'v0','mthi':'a1','mtlo':'a1',
    **{op:'v0,0(a0)' for op in ('lb','lbu','lh','lhu','lw')},
    **{op:'a1,0(a0)' for op in ('sb','sh','sw')},
    'b':'8','j':'8','jr':'ra',
    **{op:'a1,a2,8' for op in ('beq','bne','beql','bnel')},
    **{op:'a1,8' for op in ('beqz','bnez','bgez','bgtz','blez','bltz',
                            'beqzl','bnezl','bgezl','bgtzl','blezl','bltzl')},
}


def test_witnesses_cover_exact_admission_set():
    assert set(OPERANDS)==c.LEAF_OPS


@pytest.mark.parametrize('opcode',sorted(OPERANDS))
def test_admitted_opcode_executes(opcode):
    assembly=opcode+' '+OPERANDS[opcode]+'\nnop\njr ra\nnop'
    leaf=c.Leaf(assembly,'synthetic admission execution witness')
    case=d.TestCase('witness',1,entry_registers=(('a1',7),('a2',2)))
    run=d.execute_case(leaf.program,case)
    assert run.status=='returned',run.error


def test_special_register_transfer_values_and_origins():
    program=d.Program.parse('transfers','mthi a0\nmtlo a1\nmfhi v0\nmflo v1\njr ra\nnop')
    run=d.execute_case(program,d.TestCase('transfers',1,
        entry_registers=(('a0',0x87654321),('a1',0xfedcba98))),return_registers=('v0','v1'))
    assert run.status=='returned'
    assert run.return_values=={'v0':0x87654321,'v1':0xfedcba98}
    assert 'entry a0=' in run.trace[2].writes[0][2]
    assert 'entry a1=' in run.trace[3].writes[0][2]


def test_signed_negation_overflow_remains_explicitly_unsupported():
    for value in (0,1,0x7fffffff,0xffffffff,0x80000000):
        run=d.execute_case(d.Program.parse('neg','neg v0,a0\njr ra\nnop'),
            d.TestCase('neg',1,entry_registers=(('a0',value),)),return_registers=('v0',))
        if value==0x80000000:
            assert run.status=='unsupported' and 'overflow' in run.error
        else:
            assert run.status=='returned' and run.return_values['v0']==(-value)&0xffffffff
