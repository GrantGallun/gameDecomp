import importlib
import pytest

SOURCE = '#include "api.h"\nvoid f(s32 value) {\n    records.unk24 = value;\n}\n'
HEADER = 'extern Record records[4];\n'
ASM = 'lui t0,%hi(records)\naddiu t0,t0,%lo(records)\nsh a0,0x24(t0)\njr ra\nnop\n'


def run(tmp_path,source=SOURCE,header=HEADER,assembly=ASM,*,line_number=3,stale=False):
    module=importlib.import_module('solver.global_field_view')
    (tmp_path/'include').mkdir(exist_ok=True)
    (tmp_path/'include/api.h').write_text(header)
    line=source.splitlines()[line_number-1]
    column=line.index('.unk')+1
    diagnostic=f"candidate.c:{line_number}:{column}: error: member reference base type 'Record[4]' is not a structure or union\n {line_number} | {line}\n"
    if stale:diagnostic=diagnostic.replace(' | ', ' | changed ')
    return module.propose(tmp_path,source,'f',diagnostic,assembly)


def test_named_array_store_uses_matching_binary_symbol_offset_and_width(tmp_path):
    result=run(tmp_path)
    assert '(*(s16 *)((unsigned char *)&records + 0x24)) = value;' in result['source']
    assert result['changes'][0]['witnesses']==[2]
    assert result['changes'][0]['declaration']=='extern Record records[4];'


def test_header_symbolic_array_bound_uses_fresh_compiler_type(tmp_path):
    assert run(tmp_path,header='enum { COUNT = 4 };\nextern Record records[COUNT];\n')['changes']


def test_named_struct_load_uses_signedness_and_member_diagnostic(tmp_path):
    module=importlib.import_module('solver.global_field_view')
    source=SOURCE.replace('void f(s32 value)','s32 f(void)').replace('records.unk24 = value','return records.unk24')
    (tmp_path/'include').mkdir();(tmp_path/'include/api.h').write_text('extern Record records;\n')
    line=source.splitlines()[2]
    diag=f"candidate.c:3:{line.index('unk24')+1}: error: no member named 'unk24' in 'Record'\n 3 | {line}\n"
    result=module.propose(tmp_path,source,'f',diag,ASM.replace('sh a0','lhu v0'))
    assert 'return (*(u16 *)((unsigned char *)&records + 0x24));' in result['source']


@pytest.mark.parametrize('assembly',[ASM.replace('records','other'),ASM.replace('0x24','0x26'),
    ASM.replace('sh a0','lh a0'),ASM.replace('jr ra','sb a0,0x24(t0)\njr ra'),
    ASM.replace('addiu t0,t0,%lo(records)','lw t0,%lo(records)(t0)')])
def test_unbound_wrong_direction_or_ambiguous_binary_access_declines(tmp_path,assembly):
    assert not run(tmp_path,assembly=assembly)['changes']


@pytest.mark.parametrize('statement',['Record records[4]; records.unk24 = value;',
    'Record (records); records.unk24 = value;',
    'LOCAL (records); records.unk24 = value;',
    'records.unk24 += value;','++records.unk24;',
    'consume(&records.unk24);','records.unk24[0] = value;',
    'object.records.unk24 = value;'])
def test_shadow_updates_nested_objects_and_address_uses_decline(tmp_path,statement):
    assert not run(tmp_path,source=SOURCE.replace('records.unk24 = value;',statement),
                   header=HEADER+'#define LOCAL Record\n')['changes']


def test_stale_macro_pointer_and_conflicting_declarations_decline(tmp_path):
    assert not run(tmp_path,stale=True)['changes']
    for header in (HEADER+'#define records other\n',HEADER+'extern Other records[4];\n','extern Record *records;\n'):
        assert not run(tmp_path,header=header)['changes']


def test_diagnostic_edits_only_one_of_two_uses_on_the_line(tmp_path):
    result=run(tmp_path,source=SOURCE.replace('records.unk24 = value;', 'records.unk24 = value; consume(records.unk24);'))
    assert len(result['changes'])==1 and 'consume(records.unk24)' in result['source']


@pytest.mark.parametrize('statement', ['(records.unk24) = value;', '((records.unk24) ) = value;',
    'consume(sizeof(records.unk24));', 'consume(sizeof(1 + records.unk24));',
    'consume(sizeof(-records.unk24));'])
def test_parenthesized_store_and_unevaluated_access_decline(tmp_path,statement):
    assert not run(tmp_path,source=SOURCE.replace('records.unk24 = value;',statement),
                   assembly=ASM.replace('sh a0','lh v0'))['changes']
