"""Big-endian byte-lane expectations, independently enumerated from ISA semantics."""
import pytest
from solver import mips_differential as d


@pytest.mark.parametrize('op,expected',[
    ('swl',[0xAABBCCDD,0x11AABBCC,0x1122AABB,0x112233AA]),
    ('swr',[0xDD223344,0xCCDD3344,0xBBCCDD44,0xAABBCCDD])])
def test_partial_store_lanes_preserve_untouched_bytes_and_source(op,expected):
    for lane,wanted in enumerate(expected):
        program=d.Program.parse('lanes',f'li t0,0xaabbccdd\n{op} t0,{lane}(a0)\nlw v0,0(a0)\nmove v1,t0\njr ra\nnop')
        run=d.execute_case(program,d.TestCase('lane',1,player_writes=((0,4,0x11223344),)),
            return_registers=('v0','v1'))
        assert run.status=='returned',run.error
        assert run.return_values=={'v0':wanted,'v1':0xAABBCCDD}
        assert run.writes[0].width==(4-lane if op=='swl' else lane+1)
        assert run.writes[0].raw_address==d.PLAYER_BASE+(lane if op=='swl' else 0)
        assert 'high' in run.writes[0].value_provenance if op=='swl' else 'low' in run.writes[0].value_provenance


@pytest.mark.parametrize('offset',range(4))
@pytest.mark.parametrize('reverse',[False,True])
def test_store_pair_crosses_word_boundary_in_either_order(offset,reverse):
    ops=[f'swl t0,{offset}(a0)',f'swr t0,{offset+3}(a0)']
    if reverse: ops.reverse()
    asm='li t0,0xaabbccdd\n'+'\n'.join(ops)+'\nlw v0,0(a0)\nlw v1,4(a0)\njr ra\nnop'
    original=bytearray.fromhex('1122334455667788')
    original[offset:offset+4]=bytes.fromhex('aabbccdd')
    run=d.execute_case(d.Program.parse('pair',asm),d.TestCase('pair',1,
        player_writes=((0,4,0x11223344),(4,4,0x55667788))),return_registers=('v0','v1'))
    assert run.status=='returned',run.error
    assert run.return_values=={'v0':int.from_bytes(original[:4],'big'),'v1':int.from_bytes(original[4:],'big')}


@pytest.mark.parametrize('op',['swl','swr'])
def test_unmapped_partial_store_is_not_a_pass(op):
    run=d.execute_case(d.Program.parse('fault',f'{op} a1,0(a0)\njr ra\nnop'),
        d.TestCase('fault',1,entry_registers=(('a0',0),)))
    assert run.status=='memory_fault'
    assert op in run.error
