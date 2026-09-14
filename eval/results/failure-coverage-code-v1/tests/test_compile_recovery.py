from pathlib import Path
import json

from solver import compile_obligations, compile_recovery, project_headers, workspace


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


def test_storage_packet_keeps_unknown_identity_and_available_type_definitions(tmp_path):
    put(tmp_path, 'types.h', 'typedef struct Packet {int field;} Packet;\nvoid f(void *p);')
    source = '#include "types.h"\nvoid f(Packet *p) {}'
    result = compile_obligations.packet(tmp_path, 'f', source, 'glabel f\nlw v0,0(t9)\njr ra\nnop')
    assert result['memory_accesses'][0]['address'] == 'unresolved'
    assert result['read_only_header_types'][0]['type'] == 'Packet'
    assert result['public_prototypes'] == [{'header':'types.h', 'declaration':'void f(void *p)'}]
