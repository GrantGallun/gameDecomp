from solver.frontend_repair import propose

SOURCE='void f(void) {\n    values = 0;\n}\n'
DIAG="candidate.c:2:12: error: array type 'u16[4]' (aka 'unsigned short[4]') is not assignable\n    2 |     values = 0;\n"
ASM='lui at, %hi(values)\nsh zero, %lo(values)(at)\n'


def test_target_zero_store_selects_first_element(tmp_path):
    r=propose(tmp_path,SOURCE,'f',DIAG,big_endian_o32=True,target_assembly=ASM)
    assert 'values[0] = 0;' in r['source']
    assert r['array_zero_stores'][0]['width']==2


def test_requires_unique_exact_width_zero_store_and_fresh_source(tmp_path):
    for asm in [ASM.replace('sh ','sw '), ASM.replace('sh zero','sh t0'), ASM+ASM,
                ASM.replace('%lo(values)','%lo(values+2)')]:
        assert not propose(tmp_path,SOURCE,'f',DIAG,big_endian_o32=True,target_assembly=asm)['changes']
    assert not propose(tmp_path,SOURCE,'f',DIAG,target_assembly=ASM)['changes']
    assert not propose(tmp_path,SOURCE,'f',DIAG.replace('values = 0;', 'values = 1;'),big_endian_o32=True,target_assembly=ASM)['changes']
