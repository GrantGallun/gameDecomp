from solver import rewrites


SOURCE = 'int f(Acmd *start) {\n Acmd *end;\n return (s32) (end - start) >> 3;\n}'
DIFF = '-sra t0,t1,3\n+sra t0,t1,3\n+sra t0,t0,3\n'


def test_pointer_difference_scale_is_bounded_source_bound_and_wired():
    rows = rewrites.pointer_difference_scale_rewrites(SOURCE,DIFF)
    assert len(rows) == 1
    assert '(s32) (end - start);' in rows[0](SOURCE)
    assert rows[0](SOURCE+' ') == SOURCE+' '
    assert any(r.kind == 'pointer-difference-scale' for r in rewrites.propose(SOURCE,DIFF))


def test_pointer_difference_scale_requires_both_typed_operands_and_surplus_shift():
    for source in (SOURCE.replace('Acmd *end','int end'),
                   SOURCE.replace('Acmd *end','Other *end'),
                   SOURCE.replace('Acmd','char'),
                   SOURCE.replace('>> 3','>> 2'), '/*'+SOURCE+'*/'):
        assert rewrites.pointer_difference_scale_rewrites(source,DIFF) == []
    assert rewrites.pointer_difference_scale_rewrites(SOURCE,DIFF.rsplit('+sra',1)[0]) == []
    assert rewrites.pointer_difference_scale_rewrites(SOURCE,'') == []
