import pytest
from solver import global_scalar_view as repair


SOURCE = '#include "api.h"\nint f(void) {\n    return indexValue * 4;\n}\n'
HEADER = 'typedef union { short signedValue; unsigned short unsignedValue; } IndexValue;\nextern IndexValue indexValue;\n'
ASM = 'lui t0,%hi(indexValue)\nlh v0,%lo(indexValue)(t0)\njr ra\nnop\n'


def run(tmp_path, source=SOURCE, assembly=ASM, header=HEADER, stale=False):
    (tmp_path/'include').mkdir(exist_ok=True)
    (tmp_path/'include/api.h').write_text(header)
    line=source.splitlines()[2]
    diag=f"candidate.c:3:{line.index(' * ')+2}: error: invalid operands to binary expression ('IndexValue' and 'int')\n 3 | {line}\n"
    if stale:diag=diag.replace(' |     ', ' |   ')
    return repair.propose(tmp_path,source,'f',diag,assembly)


def test_motivating_union_index_uses_signed_target_halfword(tmp_path):
    result=run(tmp_path)
    assert 'return (*(s16 *)&indexValue) * 4;' in result['source']
    assert result['changes'][0]['opcode']=='lh'
    assert result['changes'][0]['witnesses']==[1]


@pytest.mark.parametrize('assembly', [ASM.replace('lh ', 'lhu '), ASM.replace('lh ', 'lb ')])
def test_width_and_signedness_follow_the_target(tmp_path, assembly):
    result=run(tmp_path,assembly=assembly)
    wanted='u16' if 'lhu ' in assembly else 's8'
    assert f'(*({wanted} *)&indexValue)' in result['source']


@pytest.mark.parametrize('assembly', [ASM.replace('indexValue','other'),
    ASM.replace('jr ra', 'lhu v1,%lo(indexValue)(t0)\njr ra'), ASM.replace('lh ', 'sh '),
    ASM.replace('%lo(indexValue)(t0)', '%lo(indexValue+2)(t0)')])
def test_unwitnessed_or_ambiguous_read_declines(tmp_path, assembly):
    assert not run(tmp_path,assembly=assembly)['changes']


@pytest.mark.parametrize('statement', ['IndexValue indexValue; return indexValue * 4;',
    'int other, indexValue; return indexValue * 4;',
    'int (*indexValue)(void); return indexValue * 4;',
    'return ++( (indexValue)) * 4;', 'return indexValue.signedValue * 4;'])
def test_shadow_updates_and_existing_member_access_decline(tmp_path, statement):
    source=SOURCE.replace('return indexValue * 4;',statement)
    assert not run(tmp_path,source=source)['changes']


def test_stale_diagnostics_and_conflicting_headers_decline(tmp_path):
    assert not run(tmp_path,stale=True)['changes']
    assert not run(tmp_path,header=HEADER+'extern OtherValue indexValue;\n')['changes']


@pytest.mark.parametrize('expression', ['indexValue * 4 + (indexValue).signedValue',
    'sizeof(indexValue) + indexValue * 4'])
def test_only_the_diagnosed_operand_changes(tmp_path,expression):
    source=SOURCE.replace('indexValue * 4',expression)
    result=run(tmp_path,source=source)
    assert expression.replace('indexValue * 4','(*(s16 *)&indexValue) * 4') in result['source']
    assert len(result['changes'])==1


def test_parenthesized_local_object_declarator_declines(tmp_path):
    source=SOURCE.replace('    return','    IndexValue (indexValue); return')
    assert not run(tmp_path,source=source)['changes']


@pytest.mark.parametrize('expression,types,expected', [
    ('4 * indexValue', "'int' and 'IndexValue'", '4 * (*(s16 *)&indexValue)'),
    ('4 * indexValue', "'IndexValue' and 'int'", None),
    ('indexValue * 4', "'IndexValue' (aka 'union IndexValue') and 'int'", '(*(s16 *)&indexValue) * 4'),
    ('consume(indexValue) * 4', "'IndexValue' and 'int'", None),
    ('(indexValue) * 4', "'IndexValue' and 'int'", None),
])
def test_operand_side_type_and_complex_boundaries(tmp_path,expression,types,expected):
    source=SOURCE.replace('indexValue * 4',expression)
    (tmp_path/'include').mkdir()
    (tmp_path/'include/api.h').write_text(HEADER)
    line=source.splitlines()[2]
    diagnostic=f"candidate.c:3:{line.index(' * ')+2}: error: invalid operands to binary expression ({types})\n 3 | {line}\n"
    result=repair.propose(tmp_path,source,'f',diagnostic,ASM)
    if expected:assert expected in result['source']
    else:assert not result['changes']


def test_bad_operator_column_declines(tmp_path):
    (tmp_path/'include').mkdir()
    (tmp_path/'include/api.h').write_text(HEADER)
    line=SOURCE.splitlines()[2]
    diagnostic=f"candidate.c:3:12: error: invalid operands to binary expression ('IndexValue' and 'int')\n 3 | {line}\n"
    assert not repair.propose(tmp_path,SOURCE,'f',diagnostic,ASM)['changes']


@pytest.mark.parametrize('declaration',['IndexValue ((indexValue));','IndexValue (indexValue[2]);'])
def test_nested_parenthesized_local_declarators_decline(tmp_path,declaration):
    source=SOURCE.replace('    return','    '+declaration+' return')
    assert not run(tmp_path,source=source)['changes']


def test_macro_local_type_may_shadow_the_global(tmp_path):
    source=SOURCE.replace('    return','    LOCAL (indexValue); return')
    assert not run(tmp_path,source=source,header=HEADER+'#define LOCAL IndexValue\n')['changes']


def test_binary_ampersand_is_not_address_of_the_right_operand(tmp_path):
    (tmp_path/'include').mkdir()
    (tmp_path/'include/api.h').write_text(HEADER)
    source=SOURCE.replace('indexValue * 4','4 & indexValue')
    line=source.splitlines()[2]
    diagnostic=f"candidate.c:3:{line.index('&')+1}: error: invalid operands to binary expression ('int' and 'IndexValue')\n 3 | {line}\n"
    result=repair.propose(tmp_path,source,'f',diagnostic,ASM)
    assert '4 & (*(s16 *)&indexValue)' in result['source']
