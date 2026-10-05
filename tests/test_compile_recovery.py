from pathlib import Path
import json
from types import SimpleNamespace
import pytest

from solver import compile_obligations, compile_recovery, project_headers, workspace


def test_byteview_redraft_locks_header_abi_and_uses_assembly_only(tmp_path,monkeypatch):
    from solver import m2c_input, type_transaction
    source='#include "public.h"\ns32 f(s32 original) { return original; }'
    put(tmp_path,'public.h','s32 f(s32 input);')
    put(tmp_path,'common.h','typedef int s32; typedef short s16; typedef unsigned char u8;')
    draft='''extern s32 g;
s32 f(s32 arg0) {
    void *p;
    p = g + arg0;
    return M2C_FIELD(p, s16 *, 0) + *(g + arg0);
}
'''
    calls=[]
    def run(repo,path,**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(returncode=0,stdout=draft,stderr=''),{}
    monkeypatch.setattr(m2c_input,'draft',run)
    candidate,report=compile_recovery.byteview_redraft(tmp_path,tmp_path,'f',source)
    assert calls==[{'valid_syntax':True}]
    assert 'extern u8 *g;' in candidate
    assert '#include "public.h"' in candidate
    assert report['public_abi']['status']=='locked'
    assert report['reference_bodies_used'] is False
    monkeypatch.setattr(type_transaction,'contract',lambda *a:{'status':'unavailable'})
    with pytest.raises(ValueError,match='unambiguous header ABI'):
        compile_recovery.byteview_redraft(tmp_path,tmp_path,'f',source)


def test_byteview_redraft_declines_changed_parameter_types(tmp_path,monkeypatch):
    from solver import m2c_input,type_transaction
    monkeypatch.setattr(type_transaction,'contract',lambda *a:{'status':'locked',
        'shape':type_transaction.signature('s32 f(s32 *p);','f')})
    monkeypatch.setattr(m2c_input,'draft',lambda *a,**k:(
        SimpleNamespace(returncode=0,stdout='s32 f(s32 p) { return p; }',stderr=''),{}))
    with pytest.raises(ValueError,match='changes public'):
        compile_recovery.byteview_redraft(tmp_path,tmp_path,'f','')


def test_internal_unknown_callback_retries_existing_header_context(tmp_path,monkeypatch):
    from solver import m2c_input,type_transaction
    monkeypatch.setattr(type_transaction,'contract',lambda *a:{'status':'locked',
        'shape':type_transaction.signature('void f(void *p);','f')})
    raw='void f(void *p) { M2C_FIELD(p, M2C_UNK (**)(), 0x2C)(); }'
    typed='void f(void *p) { camera->update(); M2C_FIELD(p, s16 *, 0) = 1; }'
    calls=[]
    def draft(*a,**kw):
        calls.append(kw)
        return SimpleNamespace(returncode=0,stdout=typed if 'context_headers' in kw else raw,stderr=''),{}
    monkeypatch.setattr(m2c_input,'draft',draft)
    candidate,report=compile_recovery.byteview_redraft(tmp_path,tmp_path,'f','#include "camera.h"\n')
    assert len(calls)==2 and calls[1]['context_headers']==('common.h','camera.h')
    assert 'camera->update()' in candidate and 'M2C_UNK' not in candidate
    assert 'byte lowering unavailable' in report['prior_abi_decline']['reason']


def test_internal_type_retry_still_rejects_public_abi_change(tmp_path,monkeypatch):
    from solver import m2c_input,type_transaction
    monkeypatch.setattr(type_transaction,'contract',lambda *a:{'status':'locked',
        'shape':type_transaction.signature('void f(void *p);','f')})
    def draft(*a,**kw):
        code='void f(int p) {}' if 'context_headers' in kw else 'void f(void *p) { M2C_FIELD(p, M2C_UNK (**)(), 0)(); }'
        return SimpleNamespace(returncode=0,stdout=code,stderr=''),{}
    monkeypatch.setattr(m2c_input,'draft',draft)
    with pytest.raises(ValueError,match='changes public'):
        compile_recovery.byteview_redraft(tmp_path,tmp_path,'f','')


@pytest.mark.parametrize('expression,accepted', [
    ('M2C_FIELD(p, s16 *, 0x20)', True),
    ('0', False),
    ('M2C_FIELD(p, void *, 0x20)', False)])
def test_local_field_reconstruction_needs_no_global_hypothesis(tmp_path,monkeypatch,expression,accepted):
    from solver import m2c_input, type_transaction
    monkeypatch.setattr(type_transaction,'contract',lambda *a:{'status':'locked',
        'shape':type_transaction.signature('s32 f(void *p);','f')})
    draft='s32 f(void *p) { return '+expression+'; }'
    monkeypatch.setattr(m2c_input,'draft',lambda *a,**k:(
        SimpleNamespace(returncode=0,stdout=draft,stderr=''),{}))
    if accepted:
        candidate,report=compile_recovery.byteview_redraft(tmp_path,tmp_path,'f','')
        assert '(*(s16 *)((u8 *)(p) + 0x20))' in candidate
        assert len(report['lowering']['fields']) == 1
        assert report['lowering']['hypotheses'] == []
    else:
        with pytest.raises(ValueError):
            compile_recovery.byteview_redraft(tmp_path,tmp_path,'f','')


@pytest.mark.parametrize('outcome', ['accepted', 'compile_error', 'abi_change'])
def test_indexed_feedback_retains_callee_context_and_public_abi(tmp_path, monkeypatch, outcome):
    from solver import callee_prototype_repair, m2c_input, type_transaction
    monkeypatch.setattr(type_transaction, 'contract', lambda *a: {'status': 'locked',
        'shape': type_transaction.signature('void f(void *p);', 'f')})
    monkeypatch.setattr(callee_prototype_repair, 'hypotheses', lambda *a: {
        'prototypes': [{'return_type': 's32', 'name': 'helper', 'parameters': ['s16']}]})
    raw = ('extern M2C_UNK table;\nvoid f(void *p) {\n'
           ' s32 *q;\n s32 i;\n q = (i * 4) + &table;\n'
           ' M2C_FIELD(p, s16 *, 0) = helper(1);\n}')
    typed = raw.replace('extern M2C_UNK table;\n', '').replace('(i * 4) + &table', '&table[i]')
    calls = []
    def draft(*args, **kwargs):
        calls.append(kwargs)
        if 'extra_declarations' not in kwargs:
            return SimpleNamespace(returncode=0, stdout=raw, stderr=''), {}
        code = typed.replace('void f(void *p)', 'void f(s32 p)') if outcome == 'abi_change' else typed
        return SimpleNamespace(returncode=int(outcome == 'compile_error'), stdout=code, stderr='failed'), {}
    monkeypatch.setattr(m2c_input, 'draft', draft)
    if outcome == 'accepted':
        candidate, report = compile_recovery.byteview_redraft(tmp_path, tmp_path, 'f', '#include "public.h"\n')
        assert 'extern s32 table[];' in candidate
        assert 'extern s32 helper(s16);' in candidate
        assert '&table[i]' in candidate and 'M2C_FIELD' not in candidate
        assert report['indexed_extern_hypotheses'][0]['source_constraints']
    else:
        with pytest.raises(ValueError, match='indexed-declaration redraft'):
            compile_recovery.byteview_redraft(tmp_path, tmp_path, 'f', '#include "public.h"\n')
    assert len(calls) == 3
    assert calls[-1]['function_prototypes'] == (('s32', 'helper', ('s16',)),)
    assert calls[-1]['context_headers'] == ('common.h', 'public.h')
    assert calls[-1]['extra_declarations'] == 'extern s32 table[];\n'


def test_direct_call_byte_offset_requires_unique_binary_address():
    source='void f(struct O *p) { alloc(p + 0x18); }'
    asm='glabel f\nmove s0,a0\naddiu a0,s0,0x18\njal alloc\nnop\njr ra\nnop\n'
    candidate,report=compile_obligations.byte_pointer_variant(source,'f',asm)
    assert 'alloc((void *)((unsigned char *)p + 0x18))' in candidate
    assert report['plans'][0]['kind']=='direct-call-byte-address'
    assert compile_obligations.byte_pointer_variant(source,'f',asm.replace('0x18','0x20'))[0]==source
    changed=source.replace('alloc(p','p++; alloc(p')
    assert compile_obligations.byte_pointer_variant(changed,'f',asm)[0]==changed
    duplicated=source.replace('alloc(p + 0x18);','alloc(p + 0x18); alloc(p + 0x18);')
    assert compile_obligations.byte_pointer_variant(duplicated,'f',asm)[0]==duplicated


@pytest.mark.parametrize('with_interface',[True,False])
def test_interface_only_redraft_does_not_require_byte_fields(tmp_path,monkeypatch,with_interface):
    from solver import callee_prototype_repair,m2c_input,type_transaction
    monkeypatch.setattr(type_transaction,'contract',lambda *a:{'status':'locked',
        'shape':type_transaction.signature('void f(void);','f')})
    prototypes=[{'return_type':'s32','name':'helper','parameters':['s16','s16']}] if with_interface else []
    monkeypatch.setattr(callee_prototype_repair,'hypotheses',lambda *a:{'prototypes':prototypes})
    calls=[]
    def draft(*args,**kwargs):
        calls.append(kwargs)
        code='void f(void) { helper(1,2); }' if kwargs.get('function_prototypes') else 'M2C_UNK helper(M2C_UNK,M2C_UNK,s32);\nvoid f(void) { helper(1,2,3); }'
        return SimpleNamespace(returncode=0,stdout=code,stderr=''),{}
    monkeypatch.setattr(m2c_input,'draft',draft)
    if with_interface:
        candidate,report=compile_recovery.byteview_redraft(tmp_path,tmp_path,'f','')
        assert 'extern s32 helper(s16, s16);' in candidate
        assert 'helper(1,2);' in candidate
        assert not report['lowering']['fields']
        assert calls[-1]['function_prototypes']==(('s32','helper',('s16','s16')),)
    else:
        with pytest.raises(ValueError,match='no supported typed-byte reconstruction'):
            compile_recovery.byteview_redraft(tmp_path,tmp_path,'f','')


def put(root, name, text):
    path = root / 'include' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_sdk_context_excludes_shim_and_resolves_internal_type(tmp_path):
    put(tmp_path, 'common.h', '')
    put(tmp_path, 'game/shim.h', 'void f(int x);\ntypedef struct Q {int a;} Q;')
    put(tmp_path, 'PR/f.h', 'void f(void);')
    put(tmp_path, 'PRinternal/state.h', 'typedef struct __State {int a;} __State;\nextern __State __states[];')
    source = '#include "common.h"\n#include "game/shim.h"\nvoid f(void) { __State *s = __states; }'
    changed, report = compile_recovery.header_variant(tmp_path, 'f', 'glabel f', source, 'build/src/ultra/f.o')
    assert 'game/shim.h' not in changed
    assert 'PR/f.h' in changed and 'PRinternal/state.h' in changed
    assert report['removed_headers'] == ['game/shim.h']


def test_macro_call_is_not_header_prototype(tmp_path):
    put(tmp_path, 'macro.h', '#define WORK() \\\n    call(); \\\n    after();\n')
    put(tmp_path, 'real.h', 'void call(void);')
    assert [d.include for d in project_headers.declarations(tmp_path, 'call')] == ['real.h']


def test_compile_recovery_reapplies_absolute_adapter_to_later_drafts(tmp_path, monkeypatch):
    (tmp_path / 'undefined_syms.txt').write_text('D_1234 = 0x1234;\n')
    (tmp_path / 'target.s').write_text('glabel f\njr ra\nnop\n')
    source = 'extern ? D_1234;\nint f(void) { return (int)&D_1234; }\n'
    monkeypatch.setattr(compile_recovery, 'header_variant', lambda r,f,a,s,t:(s, {}))
    monkeypatch.setattr(compile_recovery, 'globals_variant', lambda c,r,f,s,d:(s, {}))
    monkeypatch.setattr(compile_obligations, 'opaque_variant', lambda r,f,s,a:(s, {}))
    monkeypatch.setattr(compile_obligations, 'byte_pointer_variant', lambda s,f,a:(s, {}))
    attempt = SimpleNamespace(compiler_recipe={'target':'build/src/f.o'}, frontend={}, compiler_stderr='')
    rows, reports = compile_recovery.variants(None,tmp_path,'f',tmp_path,source,attempt)
    # Asserted by MEMBERSHIP, not by index. `source` contains `extern ? D_1234;`, which is m2c's type
    # placeholder, so the placeholder stage now legitimately offers its own candidate first -- measured
    # 2026-09-19: this test failed on `rows[0]` purely because a new stage preceded it, which is a
    # property of the pipeline's order rather than of the absolute-symbol adapter being tested here.
    # The placeholder variant is ALSO offered and the object decides between them; nothing is replaced.
    assert ('absolute-symbols', 'int f(void) { return (int)0x1234; }\n') in rows
    assert ('m2c-type-placeholder',
            'extern s32 D_1234;\nint f(void) { return (int)&D_1234; }\n') in rows
    assert any(r.get('resolved') == [('D_1234', 0x1234)] for r in reports)


def test_globals_recovery_reuses_evidence_but_never_redeclares_header_globals(tmp_path, monkeypatch):
    put(tmp_path, 'common.h', 'extern int gKnown;')
    source = '#include "common.h"\nextern ? gUnknown;\nextern ? gNoEvidence;\nvoid f(void) { gKnown=gUnknown+gNoEvidence; }'
    monkeypatch.setattr(compile_recovery.unknowns, 'symbol_table', lambda r: {})
    monkeypatch.setattr(compile_recovery.globaldecl, 'plan', lambda *a: [
        {'name':'gKnown', 'text':'extern char gKnown;'},
        {'name':'gUnknown', 'text':'extern short gUnknown;', 'cites':[42]}])
    changed, report = compile_recovery.globals_variant(None, tmp_path, 'f', source, '')
    assert 'extern char gKnown' not in changed
    assert 'extern short gUnknown;' in changed
    assert 'extern ? gUnknown;' not in changed
    assert 'extern ? gNoEvidence;' in changed
    assert report['plans'][0]['cites'] == [42]


def test_opaque_completion_preserves_tag_and_padding(tmp_path):
    put(tmp_path, 'actor.h', 'typedef struct Actor Actor;')
    source = '#include "actor.h"\nvoid f(Actor *a) { a->timer = 0; }'
    asm = 'glabel f\nlhu v0,0x2a(a0)\nsh zero,0x2a(a0)\njr ra\nnop'
    changed, report = compile_obligations.opaque_variant(tmp_path, 'f', source, asm)
    assert 'struct Actor {' in changed and 'typedef struct {' not in changed
    assert 'char pad00[0x2a];' in changed and 'u16 timer;' in changed
    assert report['plans']


def test_complete_header_type_is_never_redefined(tmp_path):
    put(tmp_path, 'actor.h', 'typedef struct Actor Actor;\nstruct Actor {short timer;};')
    source = '#include "actor.h"\nvoid f(Actor *a) { a->timer = 0; }'
    changed, report = compile_obligations.opaque_variant(tmp_path, 'f', source,
        'glabel f\nsh zero,0x2a(a0)\njr ra\nnop')
    assert changed == source and not report['plans']


def test_bare_opaque_tag_completion_preserves_public_signature(tmp_path):
    put(tmp_path,'actor.h','struct Actor;\nvoid f(struct Actor *a);')
    source = '#include "actor.h"\nvoid f(struct Actor *a) { a->unk2A = 0; }'
    asm = 'glabel f\nlhu v0,0x2a(a0)\nsh zero,0x2a(a0)\njr ra\nnop'
    changed, report = compile_obligations.opaque_variant(tmp_path,'f',source,asm)
    assert 'struct Actor {' in changed and 'u16 unk2A;' in changed
    assert 'void f(struct Actor *a)' in changed
    assert 'typedef' not in changed
    assert len(report['plans']) == 1
    assert compile_obligations.opaque_variant(tmp_path,'f',changed,asm)[0] == changed


def test_bare_tag_completion_declines_existing_definition(tmp_path):
    put(tmp_path,'actor.h','struct Actor;\nstruct Actor {short timer;};')
    source = '#include "actor.h"\nvoid f(struct Actor *a) { a->timer = 0; }'
    changed, report = compile_obligations.opaque_variant(tmp_path,'f',source,
        'glabel f\nsh zero,0x2a(a0)\njr ra\nnop')
    assert changed == source and not report['plans']


def test_missing_tag_alias_is_repaired_independently_of_ambiguous_layout(tmp_path):
    put(tmp_path, 'actor.h', 'struct Actor;\nvoid f(struct Actor *a);')
    source = '#include "actor.h"\nvoid f(Actor *a) { a->left=0; a->right=0; }'
    asm = 'glabel f\nsw zero,0x10(a0)\nsw zero,0x20(a0)\njr ra\nnop'
    changed, report = compile_obligations.opaque_variant(tmp_path,'f',source,asm)
    assert 'typedef struct Actor Actor;' in changed
    assert 'struct Actor {' not in changed
    assert report['plans'] == []
    decline = report['declined'][0]
    assert decline['source_members'] == ['left','right']
    assert decline['observed_parameter_slots']['0'] == [[16,4,'s32'],[32,4,'s32']]
    assert compile_obligations.opaque_variant(tmp_path,'f',changed,asm)[0] == changed
    clarified = changed.replace('->left','->unk10').replace('->right','->unk20')
    completed, later = compile_obligations.opaque_variant(tmp_path,'f',clarified,asm)
    assert later['plans'] and 'struct Actor {' in completed
    assert completed.count('typedef struct Actor Actor;') == 1


def test_missing_tag_alias_accompanies_accepted_layout_without_duplicate(tmp_path):
    put(tmp_path, 'actor.h', 'struct Actor;\nvoid f(struct Actor *a);')
    source = '#include "actor.h"\nvoid f(Actor *a) { a->unk10=0; }'
    asm = 'glabel f\nsw zero,0x10(a0)\njr ra\nnop'
    changed, report = compile_obligations.opaque_variant(tmp_path,'f',source,asm)
    assert 'typedef struct Actor Actor;' in changed and 'struct Actor {' in changed
    assert report['plans'] and report['tag_aliases']
    put(tmp_path, 'actor.h', 'typedef struct Actor Actor;\nvoid f(Actor *a);')
    changed, report = compile_obligations.opaque_variant(tmp_path,'f',source,asm)
    assert 'typedef struct Actor Actor;' not in changed
    assert not report['tag_aliases']


def test_byte_pointer_view_requires_binary_call_argument_correspondence():
    source = 'void f(struct Actor *a) {\n s16 *p;\n p = a + 0x24;\n use(p);\n}'
    asm = 'glabel f\naddiu a0,a0,0x24\njal use\nnop\njr ra\nnop'
    changed, report = compile_obligations.byte_pointer_variant(source,'f',asm)
    assert 'p = (s16 *)((unsigned char *)a + 0x24);' in changed
    assert report['plans'][0]['evidence'][0]['address'] == 'param0+0x24'
    assert compile_obligations.byte_pointer_variant(changed,'f',asm)[0] == changed
    assert compile_obligations.byte_pointer_variant(source,'f',asm.replace('0x24','0x28'))[0] == source
    for altered in (source.replace('use(p);','use(p); use(p);'),
                    source.replace('p = a','a++;\n p = a'),
                    source.replace('use(p);','p++; use(p);'),
                    source.replace('use(p);','use(&a); use(p);')):
        assert compile_obligations.byte_pointer_variant(altered,'f',asm)[0] == altered


def test_storage_packet_keeps_unknown_identity_and_available_type_definitions(tmp_path):
    put(tmp_path, 'types.h', 'typedef struct Packet {int field;} Packet;\nvoid f(void *p);')
    source = '#include "types.h"\nvoid f(Packet *p) {}'
    result = compile_obligations.packet(tmp_path, 'f', source, 'glabel f\nlw v0,0(t9)\njr ra\nnop')
    assert result['memory_accesses'][0]['address'] == 'unresolved'
    assert result['read_only_header_types'][0]['type'] == 'Packet'
    assert result['public_prototypes'] == [{'header':'types.h', 'declaration':'void f(void *p)'}]


def test_void_pointer_step_uses_explicit_byte_arithmetic_only_on_rejected_local():
    from solver import repair_context
    source='void f(void) {\n void *p;\n p += 0x60C;\n p -= 4;\n}\nvoid g(void) { p += 4; }'
    rows=repair_context.normalize(source,'Bad operand type for += or -=','f')
    assert len(rows)==1 and rows[0][0]=='void-pointer-byte-step'
    assert 'p = (void *)((unsigned char *)p + 0x60C);' in rows[0][1]
    assert 'p = (void *)((unsigned char *)p - 4);' in rows[0][1]
    assert rows[0][1].endswith('void g(void) { p += 4; }')
    assert repair_context.normalize(source,'other error','f')==[]
    for changed in [source.replace('void *p;', 's32 *p;'), source.replace('void *p;','volatile void *p;'),
                    source.replace('p += 0x60C;', 'p += work();').replace('p -= 4;', 'use(p -= 4);')]:
        assert repair_context.normalize(changed,'Bad operand type for += or -=','f')==[]
