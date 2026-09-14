"""Synthetic adapter-contract tests; these fixtures are not emulator captures."""
import copy
import hashlib

import pytest

from solver import project64_capture as p, runtime_capture as r


@pytest.fixture
def export(tmp_path):
    image = bytes.fromhex('80371240') + bytes(60) + bytes.fromhex('03e0000800000000')
    rom = tmp_path / 'fixture.z64'
    rom.write_bytes(image)
    plan = {'architecture': 'mips-o32-be', 'byte_order': 'big', 'function': 'fixture',
            'entry': 0x80000100, 'code_size': 8, 'rom_offset': 64,
            'rom_sha256': hashlib.sha256(image).hexdigest(), 'registers': p.register_map(),
            'ram': [{'name': 'fixture', 'kind': 'persistent', 'address': 0x80000200, 'size': 4}]}
    sample = {'paused': True, 'pc': plan['entry'], 'hi': 0, 'lo': 0, 'uhi': 0, 'ulo': 0,
              'gpr': [0] * 32, 'ugpr': [0] * 32, 'code_hex': image[64:].hex(),
              'memory': [{**plan['ram'][0], 'hex': '01020304'}]}
    # Prove a sign-extended pointer is retained at its original full width.
    sample['gpr'][4] = 0x80100000
    sample['ugpr'][4] = 0xffffffff
    raw = {'schema_version': 1, 'kind': 'project64-debug-paused-export',
           'producer_revision': p.REVISION, 'entry': plan['entry'],
           'rom_info': {'crc1': 0, 'crc2': 0}, 'samples': [sample, copy.deepcopy(sample)]}
    return raw, plan, rom


def test_valid_export_preserves_provenance_and_register_width(export):
    raw, plan, rom = export
    result = p.import_export(raw, plan, rom)
    assert result['kind'] == 'project64-stopped-entry-capture'
    assert result['registers']['a0'] == 0xffffffff80100000
    assert result['producer']['raw_sha256'] == p.evidence_schedule.fingerprint(raw)
    r.verify(result, rom)


@pytest.mark.parametrize('field', ['pc', 'register', 'memory', 'code'])
def test_second_snapshot_must_be_stable(export, field):
    raw, plan, rom = export
    sample = raw['samples'][1]
    if field == 'pc':
        sample['pc'] += 4
    elif field == 'register':
        sample['gpr'][5] = 1
    elif field == 'memory':
        sample['memory'][0]['hex'] = '01020305'
    else:
        sample['code_hex'] = '0000000000000000'
    with pytest.raises(ValueError, match='identical'):
        p.import_export(raw, plan, rom)


@pytest.mark.parametrize('field', ['gpr', 'hi', 'lo'])
def test_noncanonical_high_half_declines(export, field):
    raw, plan, rom = export
    for sample in raw['samples']:
        if field == 'gpr':
            sample['ugpr'][4] = 0
        else:
            sample['u' + field] = 1
    with pytest.raises(ValueError, match='noncanonical'):
        p.import_export(raw, plan, rom)


@pytest.mark.parametrize('mutation,match', [
    ('running', 'debug-paused'), ('wrong_pc', 'selected entry'),
    ('code', 'instructions differ'), ('ram_length', 'incomplete'),
    ('ram_address', 'RAM differs'), ('rom_header', 'header differs'),
    ('rom_file', 'ROM identity'), ('revision', 'identity'),
    ('register_map', 'full-width'), ('zero', 'zero register'),
    ('missing_register', 'incomplete'), ('bool_register', 'register half'),
])
def test_malformed_or_unbound_export_declines(export, mutation, match):
    raw, plan, rom = export
    for sample in raw['samples']:
        if mutation == 'running': sample['paused'] = False
        if mutation == 'wrong_pc': sample['pc'] += 4
        if mutation == 'code': sample['code_hex'] = '0000000000000000'
        if mutation == 'ram_length': sample['memory'][0]['hex'] = '0102'
        if mutation == 'ram_address': sample['memory'][0]['address'] += 4
        if mutation == 'zero': sample['gpr'][0] = 1
        if mutation == 'missing_register': sample['gpr'].pop()
        if mutation == 'bool_register': sample['gpr'][5] = True
    if mutation == 'rom_header': raw['rom_info']['crc1'] = 1
    if mutation == 'rom_file': rom.write_bytes(rom.read_bytes() + b'\0')
    if mutation == 'revision': raw['producer_revision'] = 'unknown'
    if mutation == 'register_map': plan['registers']['a0']['bytes'] = 4
    with pytest.raises(ValueError, match=match):
        p.import_export(raw, plan, rom)
