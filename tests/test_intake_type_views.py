from eval import intake_probe, intake_runners
from solver import frontend_diagnostics
import pytest


def context(tmp_path, source, assembly):
    (tmp_path/'include').mkdir(exist_ok=True)
    (tmp_path/'target.s').write_text(assembly)
    obj=bytearray(52);obj[:6]=b'\x7fELF\x01\x02';obj[18:20]=(8).to_bytes(2,'big');obj[36:40]=(0x1000).to_bytes(4,'big')
    (tmp_path/'target.o').write_bytes(obj)
    return dict(candidate=source,function='f',repo=str(tmp_path),target='build/f.o',workspace=str(tmp_path),target_asm_path=str(tmp_path/'target.s'))


def test_global_view_is_wired_and_fires_on_union_index(tmp_path,monkeypatch):
    source='#include "api.h"\nint f(void) {\n    return indexValue * 4;\n}\n'
    ctx=context(tmp_path,source,'lui t0,%hi(indexValue)\nlh v0,%lo(indexValue)(t0)\njr ra\nnop\n')
    (tmp_path/'include/api.h').write_text('extern IndexValue indexValue;\n')
    def observe(s,**kw):
        assert s==source and kw['full_diagnostics']
        return dict(status='rejected',diagnostics="candidate.c:3:23: error: invalid operands to binary expression ('IndexValue' and 'int')\n 3 |     return indexValue * 4;\n")
    monkeypatch.setattr(frontend_diagnostics,'analyse',observe)
    label='eval.intake_runners.global_scalars'
    assert label in intake_probe.SEQUENCE
    result=intake_runners.RUNNERS[label](ctx,{})
    assert result['changed'] and '(*(s16 *)&indexValue)' in result['source']


def test_signature_view_is_wired_and_retains_header_receipt(tmp_path,monkeypatch):
    source='#include "api.h"\ns32 f(s32 arg0) {\n    return arg0;\n}\n'
    ctx=context(tmp_path,source,'jr ra\nmove v0,a0\n')
    (tmp_path/'include/api.h').write_text('s32 f(u32);\n')
    def observe(s,**kw):
        assert s==source and kw['full_diagnostics']
        return dict(status='rejected',diagnostics="candidate.c:2:5: error: conflicting types for 'f'\n 2 | s32 f(s32 arg0) {\n")
    monkeypatch.setattr(frontend_diagnostics,'analyse',observe)
    label='eval.intake_runners.header_signature'
    assert label in intake_probe.SEQUENCE
    result=intake_runners.RUNNERS[label](ctx,{})
    assert result['changed'] and 's32 arg0 = (s32)gd_abi_arg0;' in result['source']
    assert result['detail']['plans'][0]['header_declarations']==['s32 f(u32);']


@pytest.mark.parametrize('owner', ['global_fields', 'stack_arrays'])
def test_storage_views_are_wired_and_emit_real_proposals(tmp_path,monkeypatch,owner):
    if owner=='global_fields':
        source='#include "api.h"\nvoid f(s32 value) {\n    records.unk24 = value;\n}\n'
        assembly='lui t0,%hi(records)\naddiu t0,t0,%lo(records)\nsh a0,0x24(t0)\njr ra\nnop'
        message="member reference base type 'Record[4]' is not a structure or union"
        number,anchor=3,'.'
    else:
        source='void f(void) {\n    s32 sp20;\n    sp20[0] = 1;\n    sp20[1] = 2;\n}\n'
        assembly='addiu sp,sp,-64\nsw zero,0x20(sp)\nsw zero,0x24(sp)\naddiu sp,sp,64\njr ra\nnop'
        message='subscripted value is not an array, pointer, or vector'
        number,anchor=3,'['
    ctx=context(tmp_path,source,assembly)
    (tmp_path/'include/api.h').write_text('extern Record records[4];\n')
    line=source.splitlines()[number-1]
    def observe(code,**kw):
        assert code==source and kw['full_diagnostics']
        return dict(status='rejected',diagnostics=f'candidate.c:{number}:{line.index(anchor)+1}: error: {message}\n {number} | {line}\n')
    monkeypatch.setattr(frontend_diagnostics,'analyse',observe)
    label='eval.intake_runners.'+owner
    assert label in intake_probe.SEQUENCE
    result=intake_runners.RUNNERS[label](ctx,{})
    assert result['changed'] and result['detail']['plans']
