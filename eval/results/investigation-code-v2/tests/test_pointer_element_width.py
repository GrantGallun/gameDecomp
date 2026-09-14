from solver import rewrites


DIFF = '-sll t3,t2,0x1\n+sll t3,t2,0x2\n-lh t5,0(t4)\n+lw t5,0(t4)\n'
SOURCE = 'extern s32 *gOptions;\nint f(int i) { return gOptions[i]; }'


def test_narrow_pointee_is_source_bound_and_wired():
    proposals = rewrites.pointer_element_width_rewrites(SOURCE, DIFF)
    assert len(proposals) == 1
    assert 'extern s16 *gOptions;' in proposals[0](SOURCE)
    assert proposals[0](SOURCE+' ') == SOURCE+' '
    assert any(r.kind == 'pointer-element-width' for r in rewrites.propose(SOURCE, DIFF))
    assert 'extern u16 *gOptions;' in rewrites.pointer_element_width_rewrites(SOURCE, DIFF.replace('-lh ', '-lhu '))[0](SOURCE)


def test_requires_load_scale_index_and_source_declaration():
    for diff in (DIFF.split('-lh')[0], DIFF.split('-lh')[1],
                 DIFF.replace('sll t3,t2,0x1','sll t4,t2,0x1'),
                 DIFF.replace('-lh t5','-lh t6')):
        assert rewrites.pointer_element_width_rewrites(SOURCE, diff) == []
    for source in (SOURCE.replace('gOptions[i]', '*gOptions'),
                   SOURCE.replace('extern s32 *gOptions;', '#include "options.h"'),
                   '/* '+SOURCE+' */', 'const char *s = "extern s32 *gOptions; gOptions[i]";'):
        assert rewrites.pointer_element_width_rewrites(source, DIFF) == []


def test_named_global_signedness_requires_same_operands_and_scalar_declaration():
    source = 'extern s8 gFlag;\nint f(void) { return gFlag == 0; }'
    diff = '-lbu t0,%lo(gFlag)(t0)\n+lb t0,%lo(gFlag)(t0)\n'
    rows = rewrites.global_load_signedness_rewrites(source, diff)
    assert len(rows) == 1 and 'extern u8 gFlag;' in rows[0](source)
    assert rows[0](source+' ') == source+' '
    assert any(r.kind == 'global-load-signedness' for r in rewrites.propose(source,diff))
    for altered in (diff.replace('+lb t0','+lb t1'), diff.replace('+lb ', '+lh '), diff.replace('gFlag','other')):
        assert rewrites.global_load_signedness_rewrites(source, altered) == []
    for altered in (source.replace('gFlag;', '*gFlag;'), source.replace('gFlag;', 'gFlag[4];'), '/*'+source+'*/'):
        assert rewrites.global_load_signedness_rewrites(altered, diff) == []
