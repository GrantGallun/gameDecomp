import pytest
import shutil
import subprocess

from solver import mips_differential as d


@pytest.mark.parametrize('value', [0, 0xffffffffffffffff, 0x0123456789abcdef,
                                 0x8000000000000000, 0x7ff0000000000001])
def test_doubleword_raw_bits_layout_and_trace(value):
    program = d.Program.parse('copy', 'ldc1 $f22,0(a0)\nsdc1 $f22,8(a0)\nmfc1 v0,f22\nmfc1 v1,f23\njr ra\nnop')
    case = d.TestCase('bits', 1, player_writes=((0,8,value),))
    run = d.execute_case(program, case, return_registers=('v0','v1'))
    assert run.status == 'returned', run.error
    assert run.return_values == {'v0':value & 0xffffffff, 'v1':value >> 32}
    assert run.writes[0].width == 8 and run.writes[0].value == value
    assert {'f22','f23'} <= {name for name,_,_ in run.trace[1].reads}
    assert 'high' in run.writes[0].value_provenance and 'low' in run.writes[0].value_provenance
    # Separate 32-bit stores have the same final big-endian bytes.
    other = d.Program.parse('copy', 'lw v1,0(a0)\nlw v0,4(a0)\nsw v1,8(a0)\nsw v0,12(a0)\njr ra\nnop')
    compared = d.compare_programs(program, other, case, return_registers=('v0','v1'))
    assert compared.status == 'passed', compared.first_divergence


@pytest.mark.parametrize('opcode', ['ldc1', 'sdc1'])
def test_doubleword_invalid_register_and_alignment_fail_closed(opcode):
    for register in ('f1', 'f31', 'f32', 't0'):
        run = d.execute_case(d.Program.parse('bad', f'{opcode} {register},0(a0)\njr ra\nnop'), d.TestCase('bad',1))
        assert run.status == 'unsupported'
    run = d.execute_case(d.Program.parse('bad', f'{opcode} f0,4(a0)\njr ra\nnop'), d.TestCase('bad',1))
    assert run.status == 'memory_fault' and 'unaligned' in run.error


@pytest.mark.parametrize('opcode,expected', [('add.d',0x400c000000000000),
    ('sub.d',0xbfe0000000000000), ('mul.d',0x4008000000000000),
    ('div.d',0x3fe8000000000000)])
def test_memory_to_double_arithmetic_uses_architectural_pair_order(opcode,expected):
    program = d.Program.parse('arithmetic',
        f'ldc1 f0,0(a0)\nldc1 f2,8(a0)\n{opcode} f4,f0,f2\nsdc1 f4,16(a0)\njr ra\nnop')
    # Independent IEEE-754 memory encodings of 1.5 and 2.0.
    run = d.execute_case(program,d.TestCase('arithmetic',1,
        player_writes=((0,8,0x3ff8000000000000),(8,8,0x4000000000000000))))
    assert run.status == 'returned', run.error
    assert run.writes[0].value == expected


@pytest.mark.skipif(not all(shutil.which(n) for n in ('mips-linux-gnu-as','mips-linux-gnu-objdump')), reason='MIPS tools required')
def test_big_endian_pair_order_against_assembler_macro(tmp_path):
    # MIPS-I expands double transfers into independently encoded word transfers.
    obj = tmp_path/'pair.o'
    assembled = subprocess.run(['mips-linux-gnu-as','-EB','-mips1','-o',str(obj),'-'],
        input='.set noreorder\n.text\nl.d $f22,0($a0)\ns.d $f22,8($a0)\n',
        text=True,capture_output=True,timeout=20)
    assert assembled.returncode == 0, assembled.stderr
    dump = subprocess.run(['mips-linux-gnu-objdump','-d',str(obj)],
        text=True,capture_output=True,check=True,timeout=20).stdout
    for word in ('c4970000','c4960004','e4970008','e496000c'):
        assert word in dump, dump
