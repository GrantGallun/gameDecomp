from types import SimpleNamespace
from dataclasses import replace

import pytest

from solver import mips_differential as d


def environment(assembly):
    program = d.Program.parse('dependency', assembly)
    return SimpleNamespace(leaves={'dependency':SimpleNamespace(program=program)})


def test_dependency_only_global_bytes_seeded_in_caller_context():
    caller=d.Program.parse('caller','jr ra\nnop')
    env=environment('# MIPS_DIFF_SYMBOL g 0x80100000\n# MIPS_DIFF_BYTES g 01020304\njr ra\nnop')
    case=d.TestCase('test',1)
    symbols,sizes,deps=d._execution_context((caller,),case,env)
    memory=d._seed_memory(symbols,case,sizes,program=caller,dependencies=deps)
    assert memory.read(0x80100000,4)==0x01020304


def test_dependency_addresses_and_physical_byte_conflicts_fail_closed():
    caller=d.Program.parse('caller','# MIPS_DIFF_SYMBOL g 0x80100000\njr ra\nnop')
    with pytest.raises(ValueError,match='conflicting linker'):
        d._execution_context((caller,),d.TestCase('x',1),
            environment('# MIPS_DIFF_SYMBOL g 0x80200000\njr ra\nnop'))
    a=d.Program.parse('a','# MIPS_DIFF_SYMBOL a 0x80100000\n# MIPS_DIFF_BYTES a 00\njr ra\nnop')
    b=d.Program.parse('b','# MIPS_DIFF_SYMBOL b 0x80100000\n# MIPS_DIFF_BYTES b 01\njr ra\nnop')
    env=SimpleNamespace(leaves={'a':SimpleNamespace(program=a),'b':SimpleNamespace(program=b)})
    symbols,sizes,deps=d._execution_context((caller,),d.TestCase('x',1),env)
    with pytest.raises(ValueError,match='conflicting callee initialized'):
        d._seed_memory(symbols,d.TestCase('x',1),sizes,program=caller,dependencies=deps)


def test_dependency_jump_table_seeds_and_checks_local_target_bounds():
    caller=d.Program.parse('caller','jr ra\nnop')
    for offset in (4,8):
        env=environment(f'# MIPS_DIFF_DATA table 0 {offset}\njr ra\nnop')
        env.leaves['dependency'].program=replace(env.leaves['dependency'].program,text_base=0x90000000)
        symbols,sizes,deps=d._execution_context((caller,),d.TestCase('x',1),env)
        if offset==8:
            with pytest.raises(ValueError,match='outside'):
                d._dependency_bytes(deps,symbols)
        else:
            memory=d._seed_memory(symbols,d.TestCase('x',1),sizes,program=caller,dependencies=deps)
            assert memory.read(symbols.address('table'),4)==0x90000004


def test_real_call_execution_shares_dependency_only_global_context():
    caller=d.Program.parse('caller','addiu sp,sp,-32\nsw ra,20(sp)\njal dependency\nnop\nlw ra,20(sp)\naddiu sp,sp,32\njr ra\nnop')
    assembly='# MIPS_DIFF_SYMBOL g 0x80100000\n# MIPS_DIFF_BYTES g 0000002a\nlui t0,%hi(g)\nlw v0,%lo(g)(t0)\njr ra\nnop'
    env=environment(assembly)
    spec=env.leaves['dependency']
    spec.word_pair_multiply=False
    spec.return_registers=('v0',)
    spec.authority='synthetic test only; not production admission'
    spec.identity='synthetic'
    env.outputs={}
    case=d.TestCase('test',1)
    run=d.execute_case(caller,case,return_registers=('v0',),call_arities={'dependency':0},callee_environment=env)
    assert run.status=='returned' and run.return_values=={'v0':42}, run.error
    pair=d.compare_programs(caller,caller,case,return_registers=('v0',),call_arities={'dependency':0},callee_environment=env)
    assert pair.status=='passed' and pair.target.return_values=={'v0':42}


def test_callee_table_uses_its_own_code_base_and_returns_to_caller():
    caller=d.Program.parse('caller','addiu sp,sp,-32\nsw ra,20(sp)\njal dependency\nnop\nlw ra,20(sp)\naddiu sp,sp,32\njr ra\nnop')
    assembly=('# MIPS_DIFF_DATA table 0 24\nlui t0,%hi(table)\nlw t1,%lo(table)(t0)\n'
              'jr t1\nnop\nli v0,99\nnop\nli v0,42\njr ra\nnop')
    env=environment(assembly)
    spec=env.leaves['dependency']
    spec.program=replace(spec.program,text_base=0x90000000)
    spec.word_pair_multiply=False
    spec.return_registers=('v0',)
    spec.authority='synthetic code-base test'
    spec.identity='synthetic'
    env.outputs={}
    result=d.compare_programs(caller,caller,d.TestCase('test',1),call_arities={'dependency':0},
                              return_registers=('v0',),callee_environment=env)
    assert result.status=='passed' and result.target.return_values=={'v0':42}
    assert any('0x90000018' in str(event) for event in result.target.concrete_calls)


def test_code_base_validation_and_foreign_or_unaligned_jump_refusal():
    program=d.Program.parse('p','li t0,0x80000004\njr t0\nnop')
    with pytest.raises(ValueError,match='code-address'):
        replace(program,text_base=3)
    with pytest.raises(ValueError,match='code-address'):
        replace(program,text_base=0xFFFFFFFC)
    foreign=d.execute_case(replace(program,text_base=0x90000000),d.TestCase('x',1))
    assert foreign.status=='unsupported' and 'jr target' in foreign.error
    unaligned=d.Program.parse('p','li t0,0x80000005\njr t0\nnop')
    result=d.execute_case(unaligned,d.TestCase('x',1))
    assert result.status=='unsupported' and 'unaligned' in result.error
    env=environment('# MIPS_DIFF_DATA table 0 0\njr ra\nnop')
    with pytest.raises(ValueError,match='overlaps'):
        d._execution_context((program,),d.TestCase('x',1),env)
