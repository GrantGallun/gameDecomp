import importlib
import pytest

SOURCE='''void f(void) {
    s32 sp20;
    u32 sp24;
    sp20[0] = 1;
    sp20[1] = 2;
    sp24 = 3U;
    use(&sp20);
}
'''
ASM='''addiu sp,sp,-64
sw zero,0x20(sp)
sw zero,0x24(sp)
addiu a0,sp,0x20
jal use
nop
addiu sp,sp,64
jr ra
nop
'''


def run(tmp_path,source=SOURCE,assembly=ASM,stale=False):
    module=importlib.import_module('solver.stack_scalar_arrays')
    diagnostics=''
    for number,line in enumerate(source.splitlines(),1):
        if 'sp20[' not in line:continue
        diagnostics+=f"candidate.c:{number}:{line.index('[')+1}: error: subscripted value is not an array, pointer, or vector\n {number} | {line}\n"
    if stale:diagnostics=diagnostics.replace(' | ',' | stale ')
    return module.propose(tmp_path,source,'f',diagnostics,assembly)


def test_indexed_word_storage_and_overlapping_scalar_share_one_array(tmp_path):
    result=run(tmp_path)
    assert 's32 sp20[2];' in result['source']
    assert 'u32 sp24;' not in result['source']
    assert '(*(u32 *)&sp20[1]) = 3U;' in result['source']
    assert 'use(&sp20[0]);' in result['source']
    assert result['changes'][0]['witnesses']==[1,2]


def test_prologue_can_follow_straight_line_nonstack_instructions(tmp_path):
    result=run(tmp_path,assembly='lui t0,%hi(flag)\nlbu t0,%lo(flag)(t0)\n'+ASM)
    assert 's32 sp20[2];' in result['source']


@pytest.mark.parametrize('prefix', ['beqz a0,end\nnop\n', 'addiu a1,sp,0x20\n'])
def test_frame_allocation_after_branch_or_stack_use_declines(tmp_path,prefix):
    assert not run(tmp_path,assembly=prefix+ASM)['changes']


@pytest.mark.parametrize('assembly',[ASM.replace('0x24','0x28'),ASM.replace('sw zero,0x24','sh zero,0x24'),
    ASM.replace('addiu sp,sp,-64','addiu sp,sp,-32'),ASM.replace('jal use','addiu sp,sp,-8\njal use'),
    ASM.replace('sw zero,0x24(sp)','sw zero,0x24(a0)')])
def test_wrong_extent_width_base_or_dynamic_frame_declines(tmp_path,assembly):
    assert not run(tmp_path,assembly=assembly)['changes']


@pytest.mark.parametrize('source',[SOURCE.replace('sp20[1]','sp20[index]'),
    SOURCE.replace('u32 sp24;','s16 sp24;'),SOURCE.replace('sp24 = 3U;','consume(&sp24);'),
    SOURCE.replace('use(&sp20);','use(sizeof(sp20));'),
    SOURCE.replace('use(&sp20);','sp20 = 7;'),
    SOURCE.replace('use(&sp20);','{ s32 sp20; sp20 = 1; }'),
    SOURCE.replace('sp20[1]','sp20[0]'),SOURCE.replace('sp20[1]','sp20[3]')])
def test_unsupported_aliases_scalar_uses_shadows_and_sparse_indices_decline(tmp_path,source):
    assert not run(tmp_path,source=source)['changes']


def test_stale_diagnostics_and_already_array_decline(tmp_path):
    assert not run(tmp_path,stale=True)['changes']
    assert not run(tmp_path,source=SOURCE.replace('s32 sp20;','s32 sp20[2];'))['changes']


@pytest.mark.parametrize('source', [SOURCE.replace('sp24 = 3U;', 'object.sp24 = 3U;'),
    SOURCE.replace('use(&sp20);', 'use(value & sp20);'),
    SOURCE.replace('sp24 = 3U;', 'use(&(sp24));'),
    SOURCE.replace('sp24 = 3U;', '{ unsigned (sp24); sp24 = 3U; }'),
    SOURCE.replace('sp24 = 3U;', 'sp24: use();'),
    SOURCE.replace('sp20[1]', 'sp20[01]')])
def test_member_collision_and_ambiguous_address_uses_decline(tmp_path,source):
    assert not run(tmp_path,source=source)['changes']
