from solver import gfx_packets
import pytest


PREFIX = """glabel test
lui $v1, %hi(buffer)
addiu $v1, $v1, %lo(buffer)
lw $v0, 0($v1)
/* 0 80000000 3C18E700 */ lui $t8, (0xE7000000 >> 16)
"""


def test_decodes_motivating_reversed_store_pair_without_modifying_input():
    source = PREFIX + "sw $zero, 4($v0)\nsw $t8, 0($v0)\njr $ra\nnop\n"
    result = gfx_packets.extract(source, pointer_symbol="buffer")
    assert len(result['packets']) == 1
    packet = result['packets'][0]
    assert packet['status'] == 'constant'
    assert [store['word'] for store in packet['stores']] == [0xE7000000, 0]
    assert '(0xE7000000 >> 16)' in source
    assert not gfx_packets.extract(source, pointer_symbol='other')['packets']


def test_different_pointer_loads_do_not_form_a_packet():
    source = PREFIX + "sw $zero, 4($v0)\nlw $v0, 0($v1)\nsw $t8, 0($v0)\njr $ra\nnop\n"
    assert not gfx_packets.extract(source, pointer_symbol="buffer")['packets']


def test_unknown_word_stays_unknown():
    source = PREFIX + "sw $t8, 0($v0)\nsw $a2, 4($v0)\njr $ra\nnop\n"
    packet = gfx_packets.extract(source, pointer_symbol="buffer")['packets'][0]
    assert packet['status'] == 'dynamic-word'
    assert packet['stores'][1]['word'] is None


def test_partial_store_invalidates_pending_word_pair():
    source = PREFIX + "sw $t8, 0($v0)\nsb $a2, 1($v0)\nsw $zero, 4($v0)\njr $ra\nnop\n"
    assert not gfx_packets.extract(source, pointer_symbol='buffer')['packets']


def test_cross_block_words_do_not_form_a_packet():
    source = PREFIX + "sw $t8, 0($v0)\nbeqz $a0, .Ldone\nnop\n.Ldone:\nsw $zero, 4($v0)\njr $ra\nnop\n"
    assert not gfx_packets.extract(source, pointer_symbol='buffer')['packets']


def test_expression_must_agree_with_machine_word():
    with pytest.raises(ValueError, match='disagrees'):
        gfx_packets.numeric_immediates(PREFIX.replace('3C18E700', '3C18E600'))
