from solver.frontend_repair import propose

SOURCE='void f(void) {\n    schedule(&actor, 0, 3);\n}\n'
DIAG="candidate.c:2:14: error: incompatible function pointer types passing 'void (*)(Actor *)' to parameter of type 'Callback' (aka 'void (*)(void *)') [-Wincompatible-function-pointer-types]\n    2 |     schedule(&actor, 0, 3);\n"
ASM='lui a0, %hi(actor)\naddiu a0, a0, %lo(actor)\njal schedule\nnop\n'


def test_target_callback_address_and_single_pointer_signature(tmp_path):
    r=propose(tmp_path,SOURCE,'f',DIAG,big_endian_o32=True,target_assembly=ASM)
    assert 'schedule((void (*)(void *)) &actor' in r['source']
    assert r['callback_abi_hypotheses']


def test_callback_declines_wrong_target_signature_or_abi(tmp_path):
    for asm in [ASM.replace('actor','other'), ASM+ASM]:
        assert not propose(tmp_path,SOURCE,'f',DIAG,big_endian_o32=True,target_assembly=asm)['changes']
    for diag in [DIAG.replace('Actor *','Actor *, s32'),DIAG.replace("passing 'void", "passing 's32"),DIAG.replace('2 |     schedule','2 |     other')]:
        assert not propose(tmp_path,SOURCE,'f',diag,big_endian_o32=True,target_assembly=ASM)['changes']
    assert not propose(tmp_path,SOURCE,'f',DIAG,target_assembly=ASM)['changes']


def test_expression_calls_bare_designators_and_repeated_callback(tmp_path):
    source='void f(void) {\n    if (schedule(actor, 0, 3) != 0) hit();\n    schedule(actor, 0, 3);\n}\n'
    diagnostics=[]
    for number,line in enumerate(source.splitlines(),1):
        if 'schedule' in line:
            diagnostics.append(f"candidate.c:{number}:{line.index('actor')+1}: error: incompatible function pointer types passing 'void (Actor *)' to parameter of type 'void (*)(void *)'\n {number} | {line}\n")
    r=propose(tmp_path,source,'f',''.join(diagnostics),big_endian_o32=True,target_assembly=ASM+ASM)
    assert r['source'].count('schedule((void (*)(void *)) actor')==2
    assert len({h['instruction'] for h in r['callback_abi_hypotheses']})==2
    assert not propose(tmp_path,source,'f',''.join(diagnostics),big_endian_o32=True,target_assembly=ASM)['changes']
