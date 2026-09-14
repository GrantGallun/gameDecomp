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


def test_large_linked_case_seed_replays_through_alias_and_detects_wrong_result():
    # The popup explorer produced this offset in a linker-bound large buffer.
    # Seeding must retain the same address/bytes on both comparison sides.
    assembly = '''
        lui t0,%hi(alias)
        lw v0,%lo(alias)(t0)
        jr ra
        nop
        # MIPS_DIFF_SYMBOL buffer 0x80010000
        # MIPS_DIFF_SYMBOL alias 0x800296c0
        # MIPS_DIFF_EXTENT buffer 123872
    '''
    assembly = '\n'.join(line.strip() for line in assembly.splitlines())
    case = d.TestCase('large-seed', 1,
                     global_writes=(('buffer+0x196c0', 4, 0x12345678),))
    row = d.run_suite(assembly, assembly, (case,), return_registers=('v0',))[0]
    assert row.status == 'passed'
    assert row.target.return_values == {'v0': 0x12345678}
    wrong = assembly.replace('lw v0,%lo(alias)(t0)', 'li v0,0')
    assert d.run_suite(assembly, wrong, (case,), return_registers=('v0',))[0].status == 'failed'


@pytest.mark.parametrize('linked', [False, True])
def test_large_case_seed_keeps_address_space_guards(linked):
    addresses = {'buffer': d.STACK_BASE} if linked else {}
    symbols = d.SymbolTable({'buffer'}, addresses)
    case = d.TestCase('invalid-large-seed', 1,
                     global_writes=(('buffer+0x196c0', 4, 1),))
    with pytest.raises(ValueError, match='overlaps synthetic/reserved|synthetic symbol stride'):
        d._seed_memory(symbols, case)


@pytest.mark.parametrize('address,offset', [(0xffff0000, 0x196c0),
                                          (0x80010000, 8*1024*1024)])
def test_large_case_seed_keeps_wrap_and_resource_bounds(address, offset):
    symbols = d.SymbolTable({'buffer'}, {'buffer': address})
    case = d.TestCase('invalid-extent', 1,
                     global_writes=((f'buffer+{offset:#x}', 4, 1),))
    with pytest.raises(ValueError, match='resource-limited symbol extent'):
        d._seed_memory(symbols, case)


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
