import pytest

from solver import cfg, dataflow, workspace


BODY = '''beqz a0,10
nop
jr ra
nop
lw t9,4(a0)
jalr t9
nop
jr ra
nop'''
MARKER = '# MIPS_DIFF_NUMERIC_BRANCH_BASE 0x0\n'


def test_normalized_hex_byte_offsets_recover_hidden_callback():
    assert not dataflow.analyse(BODY).callsites  # No address convention inferred.
    analysis = dataflow.analyse(MARKER+BODY)
    assert list(analysis.callsites) == [5]
    assert analysis.graph.label_to_instruction['10'] == 4
    assert not any(b.unknown_successor for b in analysis.graph.blocks.values())
    assert analysis.callsites[5].target_value.describe() == 'load(param0+0x4)'


def test_explicit_base_bounds_alignment_and_symbol_priority():
    graph=cfg.build('# MIPS_DIFF_NUMERIC_BRANCH_BASE 0x80000000\n'+BODY.replace('a0,10','a0,80000010'))
    assert graph.label_to_instruction['80000010'] == 4
    for token in ('12','1000','external'):
        graph=cfg.build(MARKER+BODY.replace('a0,10','a0,'+token))
        assert graph.blocks[0].unknown_successor
    graph=cfg.build(MARKER+BODY.replace('a0,10','a0,dead').replace('lw t9','dead:\nlw t9'))
    assert graph.label_to_instruction['dead'] == 4
    with pytest.raises(ValueError,match='conflicting'):
        cfg.build(MARKER+'# MIPS_DIFF_NUMERIC_BRANCH_BASE 0x1000\n'+BODY)


def test_workspace_semantic_adapter_marks_its_normalized_dump(tmp_path,monkeypatch):
    for name in ('_linker_symbol_addresses','_elf_jump_words','_elf_data_symbols'):
        monkeypatch.setattr(workspace,name,lambda *a:{})
    enriched=workspace.semantic_assembly(BODY,tmp_path/'unused.o')
    assert '# MIPS_DIFF_NUMERIC_BRANCH_BASE 0x0' in enriched
    assert list(dataflow.analyse(enriched).callsites)==[5]
