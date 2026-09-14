from solver.frontend_repair import propose

SOURCE='#include "api.h"\nvoid f(void) {\n    tick(&object, 20, pointer, -1);\n}\n'
DIAG='candidate.c:3:10: error: too many arguments to function call, expected 0, have 4\n    3 |     tick(&object, 20, pointer, -1);\n'
ASM='jal tick\nnop\n'


def test_zero_arity_preserves_expression_evaluation(tmp_path):
    (tmp_path/'include').mkdir()
    (tmp_path/'include/api.h').write_text('void tick(void);\n')
    r=propose(tmp_path,SOURCE,'f',DIAG,big_endian_o32=True,target_assembly=ASM)
    assert '((void)(&object), (void)(20), (void)(pointer), (void)(-1), tick());' in r['source']


def test_zero_arity_requires_header_target_and_closed_arguments(tmp_path):
    (tmp_path/'include').mkdir()
    header=tmp_path/'include/api.h'
    header.write_text('void tick(int value);\n')
    assert not propose(tmp_path,SOURCE,'f',DIAG,big_endian_o32=True,target_assembly=ASM)['changes']
    header.write_text('void tick(void);\n')
    for asm in [ASM.replace('tick','other'), ASM+ASM]:
        assert not propose(tmp_path,SOURCE,'f',DIAG,big_endian_o32=True,target_assembly=asm)['changes']
    bad=SOURCE.replace('pointer,','pointer++,')
    assert not propose(tmp_path,bad,'f',DIAG.replace('pointer,','pointer++,'),big_endian_o32=True,target_assembly=ASM)['changes']
