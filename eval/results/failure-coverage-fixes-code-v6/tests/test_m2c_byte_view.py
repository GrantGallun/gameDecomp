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


@pytest.mark.parametrize('source',[
    SOURCE.replace('s16 *, 0','s16 *, 2'),
    SOURCE.replace('return M2C_FIELD','return coords ^ M2C_FIELD'),
    SOURCE+'s32 other(void) { return coords; }',
    SOURCE+'s32 other(void) { return M2C_FIELD(0, s16 *, 0); }',
    SOURCE.replace('p = coords + index;','p = coords + index; p = 0;'),
    SOURCE.replace('return M2C_FIELD','return M2C_FIELD(p, u16 *, 0) + M2C_FIELD')])
def test_declines_missing_conflicting_or_out_of_scope_evidence(source):
    with pytest.raises(ValueError): lower(source,'f')
