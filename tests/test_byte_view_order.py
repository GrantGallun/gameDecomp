"""A scheduling generator must fire on the residual that motivated it."""
import pytest

SOURCE = '''void f(void *p, struct S *arg) {
    (*(s32 *)((unsigned char *)p + 0x20)) = 0;
    (*(s32 *)((unsigned char *)p + 0x1C)) = (s32) ((arg->word & 0xFFFF0000) | 1);
    (*(s32 *)((unsigned char *)p + 0x24)) = 0;
}
'''
DIFF = '''--- target
+++ candidate
@@ -1,7 +1,7 @@
-lw t3,0x1c(a1)
 sw zero,0x20(v0)
+lw t3,0x1c(a1)
+sw zero,0x24(v0)
 and t4,t3,a0
 ori t5,t4,0x1
 sw t5,0x1c(v0)
-sw zero,0x24(v0)
'''


def test_generator_emits_the_store_swap_that_closes_the_matrix_residual():
    from solver.byte_view_order import propose
    lines = SOURCE.splitlines(keepends=True)
    expected = ''.join([lines[0], lines[2], lines[1], *lines[3:]])
    proposals = propose(SOURCE, DIFF)
    assert any(p(SOURCE) == expected for p in proposals)
    assert all(p(SOURCE + '/* other source */') == SOURCE + '/* other source */' for p in proposals)


def test_common_rewrite_catalog_reaches_generated_byte_view_stores():
    from solver import rewrites
    assert any(p.kind == 'byte-view-order' for p in rewrites.propose(SOURCE, DIFF))


@pytest.mark.parametrize('source,diff', [
    (SOURCE, ''),
    (SOURCE, DIFF + '+nop\n'),
    (SOURCE.replace('struct S *arg', 'volatile struct S *arg'), DIFF),
    (SOURCE.replace('(s32) ((arg->word & 0xFFFF0000) | 1)', 'side_effect()'), DIFF),
    (SOURCE.replace('(s32) ((arg->word & 0xFFFF0000) | 1)', '(*fn)()'), DIFF),
    (SOURCE.replace('(s32) ((arg->word & 0xFFFF0000) | 1)', 'arg->word >>= 1'), DIFF),
    (SOURCE.replace('(s32) ((arg->word & 0xFFFF0000) | 1)', 'arg->word <<= 1'), DIFF),
    (SOURCE.replace('0x1C', '0x20').replace('0x24', '0x20'), DIFF),
    (SOURCE.replace('*)p + 0x1C', '*)q + 0x1C'), DIFF),
])
def test_declines_without_a_narrow_scheduling_witness(source, diff):
    from solver.byte_view_order import propose
    assert propose(source, diff) == []


def test_never_moves_a_store_into_or_out_of_an_unbraced_conditional():
    from solver.byte_view_order import propose
    lines = SOURCE.splitlines(keepends=True)
    source = lines[0] + '    if (arg->word)\n' + lines[1] + lines[2] + lines[-1]
    assert propose(source, DIFF) == []
