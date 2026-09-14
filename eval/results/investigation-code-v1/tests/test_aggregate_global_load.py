import hashlib
from solver.aggregate_scalar_repair import propose

SOURCE='void f(void) {\n    index = 9;\n    if (index == -1) index = 7;\n}\n'
ASM='lui t0, %hi(index)\naddiu t0, t0, %lo(index)\nlh v0, 0(t0)\nsh v0, 0(t0)\n'
DIAG="candidate.c:2:11: error: assigning to 'Index' from incompatible type 'int'\n    2 |     index = 9;\n"


def measurement(source):
    return {'kind':'target-compiler-header-layouts','source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'global_declarations':[{'name':'index','spelling':'Index'}],
        'layouts':{'Index':[{'member':'signedValue','offset':0,'width':2,'spelling':'s16'},
                            {'member':'unsignedValue','offset':0,'width':2,'spelling':'u16'}]}}


def test_signed_load_selects_measured_global_member():
    result=propose(SOURCE,'f',DIAG,measurement(SOURCE),target_assembly=ASM)
    assert result['source'].count('index.signedValue')==3
    assert result['changes'][0]['target_accesses']


def test_mixed_loads_escape_offsets_or_stale_diagnostics_decline():
    for asm in [ASM.replace('lh v0','lhu v1, 0(t0)\nlh v0'), ASM.replace('0(t0)','2(t0)'), ASM.replace('lh v0','sh v0')]:
        assert not propose(SOURCE,'f',DIAG,measurement(SOURCE),target_assembly=asm)['changes']
    assert not propose(SOURCE,'f',DIAG.replace('index = 9;', 'index = 8;'),measurement(SOURCE),target_assembly=ASM)['changes']
    source=SOURCE.replace('if (index', 'use(&index); if (index')
    assert not propose(source,'f',DIAG,measurement(source),target_assembly=ASM)['changes']


def test_zero_only_write_canonicalizes_without_signedness_inference():
    source='void f(void) {\n    index = 0;\n}\n'
    diag=DIAG.replace('index = 9;', 'index = 0;')
    asm='lui t0, %hi(index)\nsh zero, %lo(index)(t0)\n'
    result=propose(source,'f',diag,measurement(source),target_assembly=asm)
    assert 'index.unsignedValue = 0;' in result['source']
    assert result['changes'][0]['signedness_inferred'] is False
    for bad in [source.replace('index = 0;', 'index = 1;'),
                source.replace('index = 0;', 'index = 0; use(index);')]:
        assert not propose(bad,'f',diag,measurement(bad),target_assembly=asm)['changes']
