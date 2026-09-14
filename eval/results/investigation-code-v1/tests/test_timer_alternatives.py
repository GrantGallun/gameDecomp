from solver import timer_alternatives as ops

SOURCE = '''int f(int a, int b) {
    int left;
    int right;
    int diff;
    int flag;
    left = a;
    right = b;
    if (right <= left) {
        flag = 0;
        diff = left - right;
    } else {
        flag = 1;
        diff = right - left;
    }
    return diff + flag;
}
'''

def test_branch_reuses_input_without_changing_other_functions():
    other = 'int g(void) {\n    int diff;\n    diff = 1;\n    return diff;\n}\n'
    variants=ops.candidates(other+SOURCE+'int h(void) { return 1; }','f')
    reuse=next(v.source for v in variants if v.label=='absolute-difference-reuse:left:default-False')
    assert reuse.startswith(other)
    assert reuse.endswith('int h(void) { return 1; }')
    assert 'left = left - right;' in reuse
    assert 'return left + flag;' in reuse

def test_rename_preserves_literals_comments_and_members():
    text='diff + p->diff + p.diff; /* diff */ puts("diff");'
    assert ops._rename(text,'diff','left')=='left + p->diff + p.diff; /* diff */ puts("diff");'

def test_declines_condition_effects_and_escaping_locals():
    assert not ops.candidates(SOURCE.replace('right <= left','flag++ <= left'),'f')
    assert not ops.candidates(SOURCE.replace('return diff + flag;','take(&left); return diff + flag;'),'f')

def test_reuse_does_not_remove_already_reused_input():
    source=SOURCE.replace('    int diff;\n','').replace('diff','left')
    assert all('reuse:right:' not in v.label for v in ops.candidates(source,'f'))

def test_declines_live_input_and_nested_declaration():
    source=SOURCE.replace('return diff + flag;','return diff + flag + left;')
    assert all('reuse:left:' not in v.label for v in ops.candidates(source,'f'))
    assert not ops.candidates(SOURCE.replace('    left = a;','    { int extra; }\n    left = a;'),'f')

CHAIN='''int f(struct R *p, int a) {
    int value;
    int q;
    int r;
    int sec;
    int min;
    value = a;
    q = value / 25600;
    r = value % 25600;
    p->fraction = (s16)r;
    sec = q % 60;
    min = (q / 60) % 99;
    p->seconds = (s8)sec;
    p->minutes = (s8)min;
    return 0;
}'''

def test_direct_field_chain_eliminates_locals_in_store_order():
    code=next(v.source for v in ops.candidates(CHAIN,'f') if v.label.startswith('direct-field-divmod'))
    assert code.index('p->fraction') < code.index('value /= 25600') < code.index('p->seconds') < code.index('value /= 60') < code.index('p->minutes')
    assert 'int q;' not in code
    assert 'int min;' not in code

def test_chain_declines_live_or_unsigned_locals():
    assert all(not v.label.startswith('direct-field-divmod') for v in ops.candidates(CHAIN.replace('return 0;','return value;'),'f'))
    assert all(not v.label.startswith('direct-field-divmod') for v in ops.candidates(CHAIN.replace('int value;','u32 value;'),'f'))
    assert all(not v.label.startswith('direct-field-divmod') for v in ops.candidates(CHAIN.replace('int value;','unsigned int value;'),'f'))

def test_bound_and_determinism():
    assert ops.candidates(SOURCE,'f',0)==[]
    assert len(ops.candidates(SOURCE,'f',2))==2
    assert ops.candidates(SOURCE,'f')==ops.candidates(SOURCE,'f')
