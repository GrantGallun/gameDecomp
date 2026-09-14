"""Pinned initial data is useful evidence, not invented runtime state."""
import copy
import json

import pytest

from solver import binary_data as b


def build(text, **kwargs):
    return b.build({'data.s': text}, {'data.s': b.digest(text.encode())}, **kwargs)


TABLE = '''.section .data, "wa"
dlabel handlers
/* 0 80001000 80002000 */ .word Fcutoff
/* 4 80001004 80002020 */ .word Fendit
/* 8 80001008 80003000 */ .word 0x80003000
enddlabel handlers
'''
TARGET = '''glabel Fcutoff
/* 100 80002000 03E00008 */ jr $ra
/* 104 80002004 00000000 */ nop
'''


def test_audio_camera_table_entries_are_explicit_address_references_not_guessed_words():
    rom = bytes.fromhex('800020008000202080003000')
    catalog = build(TABLE, rom=rom, rom_sha256=b.digest(rom))
    region = catalog['regions'][0]
    assert region['storage'] == 'mutable_initial'
    assert region['rom_verified'] and region['bytes_hex'] == rom.hex()
    assert region['reconstructed_bytes'] == 4  # only the independent literal
    assert [r['symbol'] for r in catalog['references']] == ['Fcutoff', 'Fendit']
    assert all(not r['dispatch_proven'] and not r['abi_known'] for r in catalog['references'])
    packet = b.packet(catalog, 'Fcutoff', TARGET, b.digest(TARGET.encode()))
    assert packet['regions'][0]['relations'] == ['address_taken_data_reference']
    assert packet['regions'][0]['table_candidate'] and not packet['regions'][0]['dispatch_proven']
    assert packet['regions'][0]['reference_sites'][0]['encoded_address'] == 0x80002000


def test_independent_symbol_map_reconstructs_named_words_and_detects_wrong_symbol():
    symbols = {'Fcutoff': 0x80002000, 'Fendit': 0x80002020}
    catalog = build(TABLE, symbols=symbols, symbols_sha256=b.identity(symbols))
    assert catalog['regions'][0]['reconstruction'] == 'complete'
    assert catalog['counts']['rom_verified_bytes'] == 0
    assert catalog['counts']['typed_c_verified_bytes'] == 0
    symbols['Fcutoff'] += 4
    with pytest.raises(ValueError, match='reconstructed directive'):
        build(TABLE, symbols=symbols, symbols_sha256=b.identity(symbols))
    with pytest.raises(ValueError, match='symbol map'):
        build(TABLE, symbols=symbols, symbols_sha256='bad')


def test_rodata_literal_bytes_are_reconstructed_and_reference_is_not_ownership():
    text = '.section .rodata, "a"\ndlabel aspect\n/* 0 80001000 3FAAAAAB */ .float 1.333333373\nenddlabel aspect\n'
    rom = bytes.fromhex('3FAAAAAB')
    catalog = build(text, rom=rom, rom_sha256=b.digest(rom))
    target = TARGET + 'lui $at,%hi(aspect)\nlwc1 $f0,%lo(aspect)($at)\n'
    packet = b.packet(catalog, 'Fcutoff', target, b.digest(target.encode()))
    row = packet['regions'][0]
    assert row['storage'] == 'readonly_initial' and row['rom_verified']
    assert row['reconstruction'] == 'complete'
    assert row['relations'] == ['target_relocation_reference']
    assert 'not C sizeof' in row['extent_scope']


@pytest.mark.parametrize('mutation', ['text', 'rom_hash', 'rom_bytes', 'reconstruction'])
def test_mismatched_evidence_fails_closed(mutation):
    text = '.section .rodata\ndlabel x\n/* 0 80001000 00000001 */ .word 1\n'
    rom = bytes.fromhex('00000001')
    if mutation == 'text':
        with pytest.raises(ValueError, match='assembly hash'):
            b.build({'data.s': text + '\n'}, {'data.s': b.digest(text.encode())})
    elif mutation == 'rom_hash':
        with pytest.raises(ValueError, match='ROM hash'):
            build(text, rom=rom, rom_sha256='bad')
    elif mutation == 'rom_bytes':
        other = bytes.fromhex('00000002')
        with pytest.raises(ValueError, match='pinned ROM'):
            build(text, rom=other, rom_sha256=b.digest(other))
    else:
        with pytest.raises(ValueError, match='reconstructed directive'):
            build(text.replace('.word 1', '.word 2'))


def test_bss_never_becomes_rom_or_seeded_zero_bytes():
    text = '.section .bss, "wa"\ndlabel heap\n/* 80003000 */ .space 0x20\n'
    region = build(text)['regions'][0]
    assert region['storage'] == 'unbacked_bss' and region['size'] == 32
    assert region['bytes_hex'] is None and region['rom_offset'] is None
    assert not region['rom_verified'] and region['reconstructed_bytes'] == 0


def test_mmio_is_not_readonly_ram_even_with_rodata_section():
    region = build('.section .rodata\ndlabel reg\n/* 0 A4040010 00000000 */ .word 0\n')['regions'][0]
    assert region['storage'] == 'mmio_not_ram'


def test_gap_and_unparsed_directive_split_spans_without_guessing_padding():
    text = '''.section .rodata
dlabel x
/* 0 80001000 00000001 */ .word 1
.incbin "unread.bin"
/* 8 80001008 00000002 */ .word 2
'''
    catalog = build(text)
    assert [r['size'] for r in catalog['regions']] == [4, 4]
    assert catalog['regions'][1]['labels'] == []
    assert catalog['declines'][0]['reason'].startswith('unparsed directive')


def test_conflicting_address_alias_rejected_even_beyond_output_limit():
    text = '''.section .rodata
dlabel x
/* 0 80001000 00000001 */ .word 1
enddlabel x
dlabel y
/* 4 80001000 00000002 */ .word 2
'''
    with pytest.raises(ValueError, match='overlapping'):
        build(text, max_regions=1)


def test_consistent_alias_does_not_manufacture_conflict():
    text = '.section .rodata\ndlabel x\n/* 0 80001000 00000001 */ .word 1\nenddlabel x\ndlabel y\n/* 0 80001000 00000001 */ .word 1\n'
    catalog = build(text)
    assert len(catalog['regions']) == 2
    assert catalog['counts']['listed_bytes'] == 8
    assert catalog['counts']['file_backed_bytes'] == 4
    assert catalog['counts']['reconstructed_bytes'] == 4


def test_packet_rejects_tampered_catalog_or_target_and_does_not_bind_wrong_function():
    catalog = build(TABLE)
    wrong = TARGET.replace('80002000', '80003000')
    packet = b.packet(catalog, 'Fcutoff', wrong, b.digest(wrong.encode()))
    assert packet['regions'] == [] and packet['unresolved_incoming_references'] == 1
    with pytest.raises(ValueError, match='target assembly'):
        b.packet(catalog, 'Fcutoff', wrong, b.digest(TARGET.encode()))
    modified = copy.deepcopy(catalog)
    modified['regions'][0]['storage'] = 'readonly_initial'
    with pytest.raises(ValueError, match='catalog identity'):
        b.packet(modified, 'Fcutoff', TARGET, b.digest(TARGET.encode()))


def test_catalog_and_packet_budgets_are_explicit_and_deterministic():
    catalog = build(TABLE, max_bytes=4)
    assert catalog['counts']['omitted_regions'] == 1
    assert catalog['counts']['omitted_bytes'] == 12
    assert not catalog['references']
    catalog = build(TABLE)
    assert catalog == build(TABLE)
    packet = b.packet(catalog, 'Fcutoff', TARGET, b.digest(TARGET.encode()), max_chars=512)
    assert len(json.dumps(packet, sort_keys=True)) <= 512
    assert packet['omitted_regions'] == 1


@pytest.mark.parametrize('directive,encoded', [
    ('.byte 1, 2, 255', '0102ff'), ('.short -1, 2', 'ffff0002'),
    ('.double 1.0', '3ff0000000000000'), ('.asciiz "a\\n"', '610a00'),
])
def test_supported_literal_directives_reconstruct(directive, encoded):
    text = f'.section .rodata\ndlabel value\n/* 0 80001000 {encoded} */ {directive}\n'
    assert build(text)['regions'][0]['reconstruction'] == 'complete'


def test_writable_section_flag_prevents_readonly_admission():
    text = '.section .rodata, "wa"\ndlabel x\n/* 0 80001000 00000001 */ .word 1\n'
    assert build(text)['regions'][0]['storage'] == 'mutable_initial'


def test_candidate_bytes_compare_only_against_bound_range_without_promotion():
    text = '.section .rodata\ndlabel x\n/* 0 80001000 00000001 */ .word 1\n'
    rom = bytes.fromhex('00000001')
    catalog = build(text, rom=rom, rom_sha256=b.digest(rom))
    rid = catalog['regions'][0]['id']
    exact = b.verify_candidate(catalog, rid, rom)
    assert exact['exact_compared_bytes'] and exact['whole_region_compared']
    assert not exact['typed_c_verified'] and not exact['promoted']
    mismatch = b.verify_candidate(catalog, rid, b'\x02', offset=3)
    assert mismatch['first_difference'] == {'region_offset': 3, 'address': 0x80001003,
                                            'expected': 1, 'candidate': 2}
    assert not mismatch['whole_region_compared']
    with pytest.raises(ValueError, match='outside'):
        b.verify_candidate(catalog, rid, b'\x00\x01', offset=3)
    with pytest.raises(ValueError, match='ROM-verified'):
        other = build(text)
        b.verify_candidate(other, other['regions'][0]['id'], rom)


def test_literal_only_annotations_reconstruct_actual_corpus_byte_short_and_string_forms():
    text = '''.section .rodata
dlabel message
/* 0 80001000 */ .byte 0x41
/* 1 80001001 */ .byte 0x42
/* 2 80001002 */ .short 0x1234
/* 4 80001004 */ .asciz "C"
'''
    rom = b'AB\x12\x34C\x00'
    catalog = build(text, rom=rom, rom_sha256=b.digest(rom))
    region = catalog['regions'][0]
    assert region['bytes_hex'] == rom.hex() and region['rom_verified']
    assert region['reconstructed_bytes'] == 6
    assert len(region['spans']) == 3  # adjacent byte provenance coalesces
    assert region['spans'][0]['line_end'] == region['spans'][0]['line']+1
    assert not region['spans'][0]['annotated_bytes']
    with pytest.raises(ValueError, match='pinned ROM'):
        build(text.replace('0x41', '0x43'), rom=rom, rom_sha256=b.digest(rom))


def test_unresolved_symbol_without_encoded_bytes_is_not_guessed_from_numeric_neighbors():
    text = '.section .rodata\ndlabel table\n/* 0 80001000 */ .word callback\n'
    catalog = build(text)
    assert not catalog['regions'] and not catalog['references']
    assert catalog['counts']['unparsed_directives'] == 1
