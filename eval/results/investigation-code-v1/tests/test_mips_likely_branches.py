import pytest

from solver import mips_differential as differential


@pytest.mark.parametrize('opcode,value,taken', [
    ('beqzl', 0, True), ('beqzl', 1, False),
    ('bnezl', 0, False), ('bnezl', 1, True),
    ('bgezl', -1, False), ('bgezl', 0, True),
    ('bgtzl', 0, False), ('bgtzl', 1, True),
    ('blezl', 0, True), ('blezl', 1, False),
    ('bltzl', -1, True), ('bltzl', 0, False),
    ('beql', 7, True), ('beql', 3, False),
    ('bnel', 7, False), ('bnel', 3, True),
])
def test_likely_predicate_and_delay_slot_annulment(opcode, value, taken):
    operands = 't0,t1' if opcode in {'beql', 'bnel'} else 't0'
    assembly = f'''li v0,0
    li t0,{value}
    li t1,7
    {opcode} {operands},done
    addiu v0,v0,1
    addiu v0,v0,2
done:
    jr ra
    nop
'''
    program = differential.Program.parse('branch', assembly)
    result = differential.execute_case(program, differential.TestCase('test', 1),
                                       return_registers=('v0',))
    assert result.status == 'returned', result.error
    assert result.return_values['v0'] == (1 if taken else 2)
