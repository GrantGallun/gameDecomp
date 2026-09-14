import pytest

from solver import mips_differential as d


@pytest.mark.parametrize('opcode,left,right', [
    ('mul.s', 0x7f7fffff, 0x40000000),
    ('mul.s', 0xff7fffff, 0x40000000),
    ('div.s', 0x7f7fffff, 0x00800000),
])
def test_float32_overflow_is_unsupported_execution(opcode, left, right):
    program = d.Program.parse('overflow',
        f'lwc1 f0,0(a0)\nlwc1 f2,4(a0)\n{opcode} f4,f0,f2\n'
        'swc1 f4,8(a0)\njr ra\nnop')
    run = d.execute_case(program, d.TestCase('overflow', 1,
        player_writes=((0, 4, left), (4, 4, right))))
    assert run.status == 'unsupported'
    assert 'float32 overflow' in run.error
    assert not run.writes


def test_representable_float32_multiply_still_returns():
    program = d.Program.parse('finite',
        'lwc1 f0,0(a0)\nlwc1 f2,4(a0)\nmul.s f4,f0,f2\n'
        'swc1 f4,8(a0)\njr ra\nnop')
    run = d.execute_case(program, d.TestCase('finite', 1,
        player_writes=((0, 4, 0x3fc00000), (4, 4, 0x40000000))))
    assert run.status == 'returned'
    assert run.writes[0].value == 0x40400000
