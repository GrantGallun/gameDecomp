"""Dry schema and hash checks for the unapplied delivery manifest."""
from pathlib import Path

import pytest

import apply_amendment as amendment


def test_stage_payload_normalizes_only_verified_additional_input_hashes(tmp_path):
    staged = tmp_path/'staged'
    source = staged/'solver/binary_type_draft.py'
    source.parent.mkdir(parents=True)
    source.write_text('source')
    elf = tmp_path/'game.elf'
    elf.write_bytes(b'ELF')
    manifest = {'kind':'unapplied-campaign-delivery-stage',
                'changed':{'solver/binary_type_draft.py':{'old_sha256':None,
                                                          'new_sha256':amendment.sha(source)}},
                'additional_input_pins':{str(elf.resolve()):{'label':'elf:game.elf',
                                                             'sha256':amendment.sha(elf)}}}
    assert amendment.validate_payload(manifest, staged) == {str(elf.resolve()):amendment.sha(elf)}
    elf.write_bytes(b'changed')
    with pytest.raises(ValueError, match='additional input hash mismatch'):
        amendment.validate_payload(manifest, staged)
    elf.write_bytes(b'ELF')
    manifest['additional_input_pins'][str(elf.resolve())] = amendment.sha(elf)
    with pytest.raises(ValueError, match='invalid additional input row'):
        amendment.validate_payload(manifest, staged)


def test_stage_payload_rejects_stale_code(tmp_path):
    staged = tmp_path/'staged'
    source = staged/'eval/fast_campaign.py'
    source.parent.mkdir(parents=True)
    source.write_text('old')
    manifest = {'kind':'unapplied-campaign-delivery-stage',
                'changed':{'eval/fast_campaign.py':{'new_sha256':amendment.sha(source)}},
                'additional_input_pins':{}}
    source.write_text('new')
    with pytest.raises(ValueError, match='staged file hash mismatch'):
        amendment.validate_payload(manifest, staged)
