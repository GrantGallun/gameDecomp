import pytest
from eval import residual_patterns as mining


def record(name, diff, size=16):
    return dict(name=name,attempt_id=1,source_sha256='source',diff=diff,size=size,
                target_opcodes=['lw','lw','addiu','jr'])


def test_hunks_and_context_do_not_form_fake_sequences():
    diff='--- target\n+++ candidate\n@@ -1,3 +1,3 @@\n-lw v0,0(a0)\n+lw v1,0(a0)\n nop\n-addiu v0,v0,1\n+addiu v1,v1,1\n@@ -30 +30 @@\n-jr ra\n+jr t9\n'
    result=mining.analyse([record('f',diff)])
    assert result['patterns']['target_ngrams']==[]
    assert len(result['patterns']['replacement_signatures'])==3
    assert result['totals']['removed_instructions']==3
    assert mining.opcode('label:') is None


def test_ranking_uses_functions_and_counts_each_functions_bytes_once():
    records=[record('large','-lw v0,0(a0)\n'*40,400),
             record('small1','-andi v0,a0,1\n',16),record('small2','-andi v0,a0,1\n',20)]
    result=mining.analyse(records)
    rows=result['patterns']['target_opcodes']
    assert rows[0]['pattern']=='andi' and rows[0]['functions']==2
    assert rows[0]['target_bytes']==36
    assert rows[1]['occurrences']==40 and rows[1]['target_bytes']==400
    with pytest.raises(ValueError,match='multiple selected'):
        mining.analyse([records[0],records[0]])


def test_register_normalization_preserves_reuse_abi_and_constants():
    a=mining.signature(['lw t0,4(sp)','addu t1,t0,a0'],['lw t2,8(sp)','addu t3,t2,a0'])
    b=mining.signature(['lw t4,4(sp)','addu t5,t4,a1'],['lw t6,8(sp)','addu t7,t6,a1'])
    assert a==b and '4(sp)' in a and '8(sp)' in a
    assert mining.signature(['addu t0,t0,a0'],['addu t0,t1,a0']) != mining.signature(['addu t0,t0,a0'],['addu t0,t0,a0'])


def test_displacement_is_multiset_bounded_and_does_not_remove_raw_counts():
    result=mining.analyse([record('f','-lw v0,0(a0)\n-lw v0,0(a0)\n+lw v0,0(a0)\n')])
    assert result['totals']['textually_displaced_pairs']==1
    assert result['totals']['removed_instructions']==2
    assert result['patterns']['target_opcodes'][0]['baseline_occurrences']==2


def test_operand_family_distinguishes_stack_signedness_and_register_observations():
    assert mining.pair_family('lw v0,0x18(sp)','lw v0,0x1c(sp)')=='stack_slot_offset'
    assert mining.pair_family('lw v0,0x18(a0)','lw v0,0x1c(a0)')=='memory_offset'
    assert mining.pair_family('lh v0,0(a0)','lhu v0,0(a0)')=='load_signedness_opcode'
    assert mining.pair_family('lw v0,0(a0)','lw v1,0(a0)')=='register_operands'
    assert mining.pair_family('addiu sp,sp,0x40','addiu sp,sp,0x48')=='stack_frame_adjustment'
    assert mining.pair_family('beqz v0,100','beqz v0,200')=='control_operands'


def test_multi_instruction_blocks_are_not_zipped_into_fake_operand_pairs():
    result=mining.analyse([record('f','-lw v0,0(a0)\n-lw v1,4(a0)\n+lw v0,4(a0)\n')])
    assert result['patterns']['single_instruction_families']==[]


def test_saved_replay_verifies_its_exact_input_binding(tmp_path):
    import json
    source=tmp_path/'source'
    source.mkdir()
    raw=json.dumps([record('f','-lw v0,0(a0)\n+lw v1,0(a0)')]).encode()
    (source/'records.json').write_bytes(raw)
    (source/'report.json').write_text(json.dumps(dict(records_sha256=mining.digest(raw),
        checkpoint=12,exclusions={},unavailable=[])))
    replay=mining.replay(source/'records.json',tmp_path/'replay')
    assert replay['checkpoint']==12 and replay['totals']['functions']==1
    assert (tmp_path/'replay/records.json').read_bytes()==raw
    (source/'records.json').write_bytes(raw+b' ')
    with pytest.raises(ValueError,match='records changed'):
        mining.replay(source/'records.json',tmp_path/'bad')
