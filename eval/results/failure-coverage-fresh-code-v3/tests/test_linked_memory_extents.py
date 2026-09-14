import pytest
from solver import mips_differential as d


def test_large_linked_extent_preserves_alias_bytes_and_bounds():
    symbols=d.SymbolTable({'buffer','alias'},{'buffer':0x80010000,'alias':0x80020000})
    memory=d._seed_memory(symbols,d.TestCase('extent',1),{'buffer':123872})
    memory.write(0x80020000,4,0x12345678)
    assert memory.read(symbols.address('alias'),4)==0x12345678
    memory.read(0x80010000+123871,1)
    with pytest.raises(d.MemoryFault):
        memory.read(0x80010000+123872,1)


def test_large_extent_requires_explicit_linker_identity():
    for symbols in [d.SymbolTable({'buffer'}),d.SymbolTable({'D_80010000'})]:
        name=next(iter(symbols.addresses))
        with pytest.raises(ValueError,match='synthetic stride'):
            d._seed_memory(symbols,d.TestCase('extent',1),{name:123872})


def test_extent_metadata_is_shared_by_target_candidate_execution_context():
    target=d.Program.parse('f','jr ra\nnop\n# MIPS_DIFF_SYMBOL buffer 0x80010000\n# MIPS_DIFF_EXTENT buffer 123872\n')
    candidate=d.Program.parse('f','jr ra\nnop\n')
    symbols,sizes,_=d._execution_context((target,candidate),d.TestCase('extent',1))
    assert sizes['buffer']==123872
    for program in (target,candidate):
        memory=d._seed_memory(symbols,d.TestCase('extent',1),sizes,program=program)
        memory.read(0x80010000+123871,1)
    for suffix in ['# MIPS_DIFF_EXTENT buffer 0', '# MIPS_DIFF_EXTENT other 100',
                   '# MIPS_DIFF_EXTENT buffer 1\n# MIPS_DIFF_EXTENT buffer 2']:
        with pytest.raises(ValueError):
            d.Program.parse('f','jr ra\nnop\n# MIPS_DIFF_SYMBOL buffer 0x80010000\n'+suffix)


@pytest.mark.parametrize('address,size',[(0xffff0000,123872),(0x80010000,9*1024*1024),
                                       (d.STACK_BASE,123872),(0x30008000,123872)])
def test_large_linked_extent_rejects_wrap_resource_and_harness_collision(address,size):
    symbols=d.SymbolTable({'buffer','synthetic'},{'buffer':address})
    with pytest.raises(ValueError):
        d._seed_memory(symbols,d.TestCase('extent',1),{'buffer':size})
