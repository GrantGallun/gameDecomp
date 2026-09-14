from solver.frontend_repair import propose

SOURCE='void f(Player *p, Trigger *t) {\n    push(t + 28, 0);\n}\n'
DIAG="candidate.c:2:10: error: incompatible pointer types passing 'struct Trigger *' to parameter of type 'struct Vec3i *'\n    2 |     push(t + 28, 0);\n"
ASM='addiu a0, a1, 28\njal push\nnop\n'


def test_second_parameter_target_byte_offset(tmp_path):
    r=propose(tmp_path,SOURCE,'f',DIAG,big_endian_o32=True,target_assembly=ASM)
    assert 'push((void *)((u8 *)t + 28), 0)' in r['source']
    assert r['parameter_offset_calls'][0]['parameter']==1


def test_wrong_parameter_offset_shadow_or_stale_declines(tmp_path):
    duplicate='move s0, a1\n'+(ASM.replace('a1','s0')*2)
    for asm in [ASM.replace('a1','a2'),ASM.replace('28','40'),duplicate]:
        assert not propose(tmp_path,SOURCE,'f',DIAG,big_endian_o32=True,target_assembly=asm)['changes']
    for source in [SOURCE.replace('    push','    t = other;\n    push'),SOURCE.replace('    push','    Trigger *t;\n    push')]:
        assert not propose(tmp_path,source,'f',DIAG,big_endian_o32=True,target_assembly=ASM)['changes']
