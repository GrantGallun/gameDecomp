from solver import byte_array_decay as r, frontend_repair


def test_stack_byte_array_decay_and_missing_witness():
    source='void f(void) {\n u8 sp20[16];\n u8 *p;\n p = &sp20 + index;\n consume(&sp20 + index);\n}\n'
    diag="candidate.c:4:4: error: incompatible pointer types assigning to 'u8 *' from 'u8 (*)[16]'\n 4 |  p = &sp20 + index;\n"
    asm='f:\naddiu sp, sp, -64\naddiu s0, sp, 32\naddu t0, s0, a1\njr ra\naddiu sp, sp, 64\n'
    result=r.propose(source,'f',asm,diag)
    assert 'p = sp20 + index' in result['source']
    assert 'consume(sp20 + index)' in result['source']
    assert not r.propose(source,'f',asm.replace('sp, 32','sp, 36'),diag)['changes']
    assert not r.propose(source.replace('p = &','p =  &'),'f',asm,diag)['changes']


def test_pointer_word_load_requires_target_call_value(tmp_path):
    source='void f(void *a) {\n consume((*(s32 *)((unsigned char *)a + 0x8)));\n}\n'
    diag="candidate.c:2:10: error: incompatible integer to pointer conversion passing 's32' to parameter of type 'void *'\n 2 |  consume((*(s32 *)((unsigned char *)a + 0x8)));\n"
    asm='f:\nlw a0, 8(a0)\njal consume\nnop\njr ra\nnop\n'
    result=frontend_repair.propose(tmp_path,source,'f',diag,big_endian_o32=True,target_assembly=asm)
    assert '(*(void **)' in result['source']
    for target in [asm.replace('8(a0)','12(a0)'),asm.replace('lw a0','lw a1'),'']:
        assert not frontend_repair.propose(tmp_path,source,'f',diag,big_endian_o32=True,target_assembly=target)['changes']
