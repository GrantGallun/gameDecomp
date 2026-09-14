"""The measured stack-home motif must fire narrowly through normal rewrites."""
import pytest

from patterns.catalog import CATALOG
from solver import rewrites

SOURCE = '''void f(void *arg0) {
    s32 result;

    result = first(0);
    second(0);
    if (result == 1) {
        finish(arg0);
    }
}
'''
DIFF = '''--- target
+++ candidate
@@ -1,12 +1,12 @@
 addiu sp,sp,-0x20
 sw ra,0x14(sp)
 sw a0,0x20(sp)
 jal first
 move a0,zero
-sw v0,0x18(sp)
+sw v0,0x1c(sp)
 jal second
 move a0,zero
-lw t6,0x18(sp)
+lw t6,0x1c(sp)
 li at,1
 bne t6,at,48
'''


def test_normal_generator_fires_one_source_bound_candidate():
    candidates = rewrites.stack_home_padding_rewrites(SOURCE, DIFF)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.kind == 'stack-home'
    changed = candidate(SOURCE)
    assert changed.index('volatile unsigned char gd_stack_home_pad[4];') < changed.index('s32 result;')
    assert changed.count('gd_stack_home_pad') == 1
    assert candidate(SOURCE + '\n') == SOURCE + '\n'
    assert any(r.kind == 'stack-home' and r(SOURCE) == changed for r in rewrites.propose(SOURCE, DIFF))
    assert rewrites.frame_padding_rewrites(SOURCE, DIFF) == []
    assert not CATALOG['single-local-stack-home-padding'].is_hypothesis


@pytest.mark.parametrize('diff', [
    DIFF.replace('+lw t6,0x1c(sp)', '+lw t7,0x1c(sp)'),
    DIFF.replace('+sw v0,0x1c(sp)', '+sh v0,0x1c(sp)'),
    DIFF.replace('+lw t6,0x1c(sp)', '+lw t6,0x20(sp)'),
    DIFF.replace('0x18(sp)', '0x1c(sp)').replace('0x1c(sp)', '0x20(sp)'),
    DIFF.replace('0x18(sp)', '0x10(sp)'),
    DIFF.replace(' sw ra,0x14(sp)', '-sw ra,0x14(sp)\n+sw ra,0x1c(sp)'),
    DIFF + '-addiu sp,sp,-0x20\n+addiu sp,sp,-0x28\n',
    DIFF + '+nop\n',
    DIFF.replace('-lw t6,0x18(sp)\n+lw t6,0x1c(sp)\n', ''),
    DIFF.replace('(sp)', '(a0)'),
    '',
])
def test_declines_other_changes_or_unpaired_homes(diff):
    assert rewrites.stack_home_padding_rewrites(SOURCE, diff) == []


@pytest.mark.parametrize('source', [
    SOURCE.replace('s32 result;', 's32 result; s32 other;'),
    SOURCE.replace('s32 result;', 's32 result;\n\n    s32 other;'),
    SOURCE.replace('s32 result;', 'volatile s32 result;'),
    SOURCE.replace('s32 result;', 's32 result = first(0);'),
    SOURCE.replace('s32 result;', 's32 result[2];'),
    SOURCE.replace('s32 result;', 's32 *result;'),
    SOURCE.replace('s32 result;', 'u8 gd_stack_home_pad[4];\n    s32 result;'),
    SOURCE.replace('first(0)', 'first(&result)'),
    SOURCE.replace('first(0)', 'first(&(result))'),
    SOURCE.replace('first(0)', 'first(&((result)))'),
    SOURCE + '\nvoid helper(void) {}\n',
    SOURCE.replace('s32 result;', 'u8 fixed[8];\n    s32 result;'),
])
def test_declines_ambiguous_or_previously_padded_source(source):
    assert rewrites.stack_home_padding_rewrites(source, DIFF) == []


def test_rejects_ambiguous_alignment_even_for_superficially_matching_offsets(monkeypatch):
    monkeypatch.setattr(rewrites.diffrepair, 'aligned_pairs', lambda diff: [])
    assert rewrites.stack_home_padding_rewrites(SOURCE, DIFF) == []


def test_comments_do_not_become_functions_or_address_escapes():
    source = SOURCE.replace('s32 result;', '/* fake(void) { &result; } */\n    s32 result;')
    assert rewrites.stack_home_padding_rewrites(source, DIFF)


def test_objdump_column_spacing_preserves_real_generator_firing():
    assert rewrites.stack_home_padding_rewrites(SOURCE, DIFF.replace('sw v0', 'sw    v0').replace('lw t6', 'lw    t6'))


def test_mixed_residual_declines_before_paying_for_alignment(monkeypatch):
    def expensive_alignment(diff):
        pytest.fail('mixed residual should fail the cheap shape gate')
    monkeypatch.setattr(rewrites.diffrepair, 'aligned_pairs', expensive_alignment)
    assert rewrites.stack_home_padding_rewrites(SOURCE, DIFF + '-li at,1\n+li at,2\n') == []
