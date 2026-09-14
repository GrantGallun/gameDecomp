from solver.source_object_bounds import obligations


def test_scalar_view_extent_is_source_bound_and_not_a_reachability_claim():
    source='''void f(void) {
    s16 small;
    (*(s32 *)((u8 *)(&small) + 0x1C)) = 0;
}
'''
    rows=obligations(source,'f')
    assert len(rows)==1
    assert rows[0]['declared_bytes']==2 and rows[0]['required_end_offset']==32
    assert rows[0]['accesses'][0]['line']==3
    assert 'not callee behavior or path proof' in rows[0]['authority']
    assert not obligations(source.replace('s16 small;','Transform3D small;'),'f')


def test_in_bounds_comments_and_other_functions_do_not_trigger():
    source='''void f(void) {
    s32 value;
    (*(s16 *)((u8 *)(&value) + 2)) = 0;
    /* (*(s32 *)((u8 *)(&value) + 40)) = 0; */
}
void g(void) {
    s16 value;
    (*(s32 *)((u8 *)(&value) + 40)) = 0;
}
'''
    assert not obligations(source,'f')
    assert obligations(source,'g')[0]['required_end_offset']==44


def test_diagnostic_parse_decline_does_not_disable_execution():
    assert obligations('unmodeled definition','f')[0]['kind']=='source-object-analysis-unavailable'
