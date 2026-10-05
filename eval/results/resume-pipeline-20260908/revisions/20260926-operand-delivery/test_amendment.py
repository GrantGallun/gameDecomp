"""Dry scope/hash checks; no native campaign or frozen tree is touched."""
from pathlib import Path

import pytest

import apply_amendment as amendment
import stage


def payload(tmp_path):
    staged = tmp_path / 'staged'
    changed = {}
    for rel in stage.CHANGED:
        file = staged / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(rel)
        changed[rel] = {'old_sha256': None if rel in stage.NEW else 'old',
                        'new_sha256': amendment.sha(file)}
    linker_map = tmp_path / 'repo/build/snowboardkids.map'
    linker_map.parent.mkdir(parents=True)
    linker_map.write_text('map evidence')
    manifest = {'kind': 'unapplied-operand-delivery-stage', 'changed': changed,
                'additional_input_pins': {
                    str(linker_map.resolve()): {'label': stage.MAP_LABEL,
                                                'sha256': amendment.sha(linker_map)}}}
    return staged, linker_map, manifest


def test_payload_accepts_exact_seven_files_and_one_pinned_map(tmp_path):
    staged, linker_map, manifest = payload(tmp_path)
    assert amendment.validate_payload(manifest, staged) == {
        str(linker_map.resolve()): amendment.sha(linker_map)}


def test_payload_rejects_stale_code_extra_code_and_map_drift(tmp_path):
    staged, linker_map, manifest = payload(tmp_path)
    (staged / stage.CHANGED[0]).write_text('changed')
    with pytest.raises(ValueError, match='staged file hash mismatch'):
        amendment.validate_payload(manifest, staged)
    manifest['changed']['solver/unreviewed.py'] = {'old_sha256': None, 'new_sha256': 'x'}
    with pytest.raises(ValueError, match='seven-file scope'):
        amendment.validate_payload(manifest, staged)
    del manifest['changed']['solver/unreviewed.py']
    manifest['changed'][stage.CHANGED[0]]['new_sha256'] = amendment.sha(staged / stage.CHANGED[0])
    linker_map.write_text('different evidence')
    with pytest.raises(ValueError, match='additional input hash mismatch'):
        amendment.validate_payload(manifest, staged)


def test_payload_rejects_other_new_input_or_extra_pin(tmp_path):
    staged, linker_map, manifest = payload(tmp_path)
    row = manifest['additional_input_pins'][str(linker_map.resolve())]
    row['label'] = 'elf:game.elf'
    with pytest.raises(ValueError, match='invalid additional input row'):
        amendment.validate_payload(manifest, staged)
    row['label'] = stage.MAP_LABEL
    extra = tmp_path / 'repo/build/game.elf'
    extra.write_bytes(b'ELF')
    manifest['additional_input_pins'][str(extra.resolve())] = {
        'label': stage.MAP_LABEL, 'sha256': amendment.sha(extra)}
    with pytest.raises(ValueError, match='exactly one operand map'):
        amendment.validate_payload(manifest, staged)
