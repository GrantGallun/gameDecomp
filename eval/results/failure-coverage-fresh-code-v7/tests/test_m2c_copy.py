from solver import m2c_copy, repair_context


SOURCE = '''void f(Actor *arg0) {
    int offset;
    M2C_MEMCPY_ALIGNED((*(void **)((u8 *)(arg0) + 0x18)) + offset, &identity, 0x40);
}
'''


def test_lower_copy_with_once_evaluated_addresses_and_byte_offset():
    r=m2c_copy.propose(SOURCE,'f')
    assert len(r['changes'])==1
    assert '(*(unsigned char **)((u8 *)(arg0) + 0x18)) + offset' in r['source']
    assert 'm2c_copy_i < 16' in r['source']
    assert r['source'].count('&identity')==1
    assert r['source'].count(' + offset')==1
    assert not m2c_copy.propose(r['source'],'f')['changes']


def test_declines_side_effects_sizes_names_and_custom_operations():
    for source in [SOURCE.replace('&identity','get_source()'),
                   SOURCE.replace(' + offset',' + offset++'),
                   SOURCE.replace('0x40','3'), SOURCE.replace('0x40','0'),
                   SOURCE.replace('0x40','8192'), SOURCE.replace('0x40','count'),
                   SOURCE.replace('int offset','int m2c_copy_i'),
                   '#define M2C_MEMCPY_ALIGNED(a,b,c) custom(a,b,c)\n'+SOURCE]:
        assert not m2c_copy.propose(source,'f')['changes']


def test_function_scope_and_multiple_block_local_copies():
    source=SOURCE.replace('    int offset;', '    int offset;\n    M2C_MEMCPY_ALIGNED(dst, src, 4);')
    r=m2c_copy.propose(source,'f')
    assert len(r['changes'])==2
    other='\nvoid g(void) { ordinary(); }\n'
    assert m2c_copy.propose(source+other,'f')['source'].endswith(other)


def test_loaded_void_pointer_offsets_keep_pointer_result():
    source='''void f(Actor *a) {
    consume((*(void **)((u8 *)(a) + 0x18)) + (index << 6), other);
}
'''
    rows=dict(repair_context.normalize(source,'Unacceptable operand','f'))
    assert '(void *)((*(unsigned char **)((u8 *)(a) + 0x18)) + (index << 6))' in rows['void-loaded-pointer-byte-arithmetic']
    assert not repair_context.normalize(source,'','f')
    assert not repair_context.normalize(source.replace('void **','Mtx **'),'Unacceptable operand','f')
    assert not repair_context.normalize(source.replace('(index << 6)','next()'),'Unacceptable operand','f')


def test_void_parameter_pointer_assignment_uses_bytes():
    source='void f(void *arg0) {\n    Vec3i *p;\n    p = arg0 + 0xBC;\n}\n'
    rows=dict(repair_context.normalize(source,'Unacceptable operand','f'))
    assert 'p = (void *)((unsigned char *)arg0 + 0xBC);' in rows['void-parameter-byte-assignment']
    for s in [source.replace('void *arg0','Actor *arg0'),source.replace('Vec3i *p','s32 p'),
              source.replace('    Vec3i *p;','    Vec3i *p;\n    void *arg0;')]:
        assert 'void-parameter-byte-assignment' not in dict(repair_context.normalize(s,'Unacceptable operand','f'))
