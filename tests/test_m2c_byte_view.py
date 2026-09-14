import pytest
from solver.m2c_byte_view import lower


SOURCE='''extern s32 coords;
s32 f(s32 index) {
    void *p;
    p = coords + index;
    return M2C_FIELD(p, s16 *, 0) + *(coords + index);
}
'''


CURSOR = '''extern s32 object[];
void f(void) {
    M2C_UNK *p;
    p = &object;
    for (;;) {
        p += 1;
        (*(u8 *)((u8 *)(p) + 9)) = 1;
    }
}
'''


def test_unknown_byte_cursor_coordinates_type_and_address_cast():
    from solver.m2c_byte_view import unknown_local_cursors
    report = unknown_local_cursors(CURSOR, 'f')
    assert len(report['changes']) == 1
    assert 'u8 *p;' in report['source'] and 'p = (u8 *)&object;' in report['source']
    assert 'p += 1;' in report['source']
    assert report['changes'][0]['source_constraints']
    assert not unknown_local_cursors(report['source'], 'f')['changes']


def test_header_object_cursor_and_explicit_address_comparison():
    from solver.m2c_byte_view import unknown_local_cursors
    source = CURSOR.replace('extern s32 object[];', '').replace('p += 1;', 'p += 1;\n if ((u32) p < (u32) limit) break;')
    declarations = {'object': ['extern SaveBuffer object;']}
    report = unknown_local_cursors(source, 'f', header_declarations=declarations)
    assert report['changes'] and 'u8 *p;' in report['source']
    assert report['changes'][0]['header_seed_declaration'] == declarations['object'][0]
    assert not unknown_local_cursors(source, 'f')['changes']
    for decl in ['extern volatile SaveBuffer object;', 'extern SaveBuffer *object;', 'extern M2C_UNK object;']:
        assert not unknown_local_cursors(source, 'f', header_declarations={'object':[decl]})['changes']
    for bad in [source.replace('void f(void)', 'void f(s32 object)'),
                source.replace('void f(void)', 'void f(s32 p)'),
                source.replace('p += 1;', 'p += 1; use(p);')]:
        assert not unknown_local_cursors(bad, 'f', header_declarations=declarations)['changes']


@pytest.mark.parametrize('before,after', [
    ('extern s32 object[];', 'extern M2C_UNK object;'),
    ('extern s32 object[];', 'extern volatile s32 object[];'),
    ('extern s32 object[];', 'extern s32 *object;'),
    ('p += 1;', 'p += amount();'),
    ('p += 1;', 'p += 1; use(p);'),
    ('p += 1;', 'p += 1; use(&p);'),
    ('p += 1;', 'p += 1; *p = 1;'),
    ('p = &object;', 'p = &object;\n p = &object;'),
    ('M2C_UNK *p;', 'M2C_UNK *p;\n u8 *p;'),
    ('(*(u8 *)', '(*(u16 *)'),
])
def test_unknown_byte_cursor_rejects_mixed_or_effectful_uses(before, after):
    from solver.m2c_byte_view import unknown_local_cursors
    source = CURSOR.replace(before, after)
    report = unknown_local_cursors(source, 'f')
    assert not report['changes'] and report['source'] == source


def test_lowers_byte_fields_and_unanimous_bare_access_without_records():
    row=lower(SOURCE,'f')
    assert 'extern u8 *coords;' in row['source']
    assert '(*(s16 *)((u8 *)(p) + 0))' in row['source']
    assert '*((s16 *)(coords + index))' in row['source']
    assert row['aliases']['p']=='coords'
    assert len(row['hypotheses'])==2


def test_nested_macros_expand_inside_out():
    source=SOURCE.replace('*(coords + index)','M2C_FIELD((coords + M2C_FIELD(p, s16 *, 0)), s16 *, 2)')
    result=lower(source,'f')['source']
    assert 'M2C_FIELD' not in result
    assert result.count('(*(s16 *)')==3


def test_unrelated_scalar_globals_do_not_block_pointer_or_local_fields():
    source = 'extern s32 count;\n'+SOURCE.replace('return M2C_FIELD', 'count = count + 1;\n    return count + M2C_FIELD')
    source += '\ns32 other(void) { return count; }\n'
    r = lower(source, 'f')
    assert 'extern s32 count;' in r['source']
    assert 'count = count + 1;' in r['source']
    assert 'extern u8 *coords;' in r['source']
    assert 'count' not in r['aliases']
    source = 'extern s32 count;\nvoid f(void *p) { count = M2C_FIELD(p, s16 *, 0); }'
    r = lower(source, 'f')
    assert r['fields'] and not r['hypotheses']
    assert 'extern s32 count;' in r['source']


def test_header_pointer_fields_and_negative_byte_offsets():
    source='void f(void *p) { M2C_FIELD(p, Mtx **, -0x10) = M2C_FIELD(p, void **, 4); }'
    with pytest.raises(ValueError,match='unsupported field'):
        lower(source,'f')
    result=lower(source,'f',known_types={'Mtx'})['source']
    assert '(*(Mtx **)((u8 *)(p) + -0x10))' in result
    assert '(*(void **)((u8 *)(p) + 4))' in result
    with pytest.raises(ValueError,match='unsupported field'):
        lower('void f(void *p) { M2C_FIELD(p, void *, 0); }','f')


def test_opaque_pointer_placeholder_does_not_invent_a_pointee():
    row=lower('void f(void *p) { M2C_FIELD(p, M2C_UNK **, 4) = 0; }','f')
    assert '(*(void **)((u8 *)(p) + 4))' in row['source']
    assert row['hypotheses'][0]['kind']=='opaque-pointer-field-view'
    with pytest.raises(ValueError,match='unsupported field'):
        lower('void f(void *p) { M2C_FIELD(p, M2C_UNK *, 4) = 0; }','f')


@pytest.mark.parametrize('source',[
    SOURCE.replace('s16 *, 0','s16 *, 2'),
    SOURCE.replace('return M2C_FIELD','return coords ^ M2C_FIELD'),
    SOURCE+'s32 other(void) { return coords; }',
    SOURCE+'s32 other(void) { return M2C_FIELD(0, s16 *, 0); }',
    SOURCE.replace('p = coords + index;','p = coords + index; p = 0;'),
    SOURCE.replace('return M2C_FIELD','return M2C_FIELD(p, u16 *, 0) + M2C_FIELD')])
def test_declines_missing_conflicting_or_out_of_scope_evidence(source):
    with pytest.raises(ValueError): lower(source,'f')
