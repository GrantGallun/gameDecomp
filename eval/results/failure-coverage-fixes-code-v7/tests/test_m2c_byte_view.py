import pytest
from solver.m2c_byte_view import lower


SOURCE='''extern s32 coords;
s32 f(s32 index) {
    void *p;
    p = coords + index;
    return M2C_FIELD(p, s16 *, 0) + *(coords + index);
}
'''


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
