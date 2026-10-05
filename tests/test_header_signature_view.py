import pytest
from solver import header_signature_view as repair, type_transaction


SOURCE='#include "api.h"\ns32 f(struct Draft *arg0, s32 arg1) {\n    return arg0->unk8 + arg1;\n}\n'


def run(tmp_path, source=SOURCE, header='s32 f(Actual *state, u32 value);', **kwargs):
    (tmp_path/'include').mkdir(exist_ok=True)
    (tmp_path/'include/api.h').write_text(header+'\n')
    line=source.splitlines()[1]
    diag=f"candidate.c:2:5: error: conflicting types for 'f'\n 2 | {line}\n"
    return repair.propose(tmp_path,source,'f',diag,big_endian_o32=kwargs.get('abi',True))


def test_public_signature_uses_header_and_body_keeps_typed_aliases(tmp_path):
    result=run(tmp_path)
    assert result['changes']
    assert 'Actual * gd_abi_arg0' in result['source']
    assert 'struct Draft * arg0 = (struct Draft *)gd_abi_arg0;' in result['source']
    assert 's32 arg1 = (s32)gd_abi_arg1;' in result['source']
    assert 'return arg0->unk8 + arg1;' in result['source']
    type_transaction.validate(SOURCE,result['source'],'f',type_transaction.contract(tmp_path,SOURCE,'f'))


def test_pointer_to_word_return_gets_explicit_cast(tmp_path):
    source=SOURCE.replace('s32 f','u8 *f').replace('return arg0->unk8 + arg1;', 'return (u8 *)arg0 + arg1;')
    result=run(tmp_path,source=source)
    assert 'return (s32)((u8 *)arg0 + arg1);' in result['source']


@pytest.mark.parametrize('header', ['s32 f(Actual *);', 's32 f(u64, s32);',
    'double f(Actual *, u32);', 's32 f(Unknown, u32);',
    's32 f(Actual *, u32);\ns32 f(Other *, u32);'])
def test_missing_wide_unknown_or_ambiguous_contract_declines(tmp_path, header):
    assert not run(tmp_path,header=header)['changes']


def test_wrong_abi_or_body_directives_decline(tmp_path):
    assert not run(tmp_path,abi=False)['changes']
    assert not run(tmp_path,source=SOURCE.replace('    return', '#if FLAG\n    return'))['changes']


def test_generated_parameter_names_avoid_existing_identifiers(tmp_path):
    source=SOURCE.replace('    return','    s32 gd_abi_arg0;\n    return')
    result=run(tmp_path,source=source)
    assert 'Actual * gd_abi_arg0_1' in result['source']
