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
    assert rows[0] == ('absolute-symbols', 'int f(void) { return (int)0x1234; }\n')
    assert reports[0]['resolved'] == [('D_1234', 0x1234)]


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
