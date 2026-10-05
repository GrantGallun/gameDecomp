"""Dry scope/hash checks; no native campaign or frozen tree is touched."""
import pytest

import apply_amendment as amendment
import stage


def payload(tmp_path, dry=False):
    staged = tmp_path / 'staged'
    changed = {}
    for rel in stage.CHANGED:
        file = staged / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(rel)
        changed[rel] = {'old_sha256': None if rel in stage.NEW else 'old', 'new_sha256': amendment.sha(file)}
    kind = 'dry-regalloc-keyed-stage' if dry else 'unapplied-regalloc-keyed-stage'
    return staged, {'kind': kind, 'dry': dry, 'inflight_at_stage': False, 'changed': changed}


def test_payload_accepts_exactly_the_four_reviewed_files(tmp_path):
    staged, manifest = payload(tmp_path)
    amendment.validate_payload(manifest, staged)


def test_a_dry_or_inflight_stage_is_never_applicable(tmp_path):
    staged, manifest = payload(tmp_path, dry=True)
    with pytest.raises(ValueError, match='non-dry'):
        amendment.validate_payload(manifest, staged)
    staged, manifest = payload(tmp_path / 'b')
    manifest['inflight_at_stage'] = True
    with pytest.raises(ValueError, match='in flight'):
        amendment.validate_payload(manifest, staged)


def test_payload_rejects_changed_bytes_and_extra_scope(tmp_path):
    staged, manifest = payload(tmp_path)
    (staged / stage.CHANGED[0]).write_text('changed after staging')
    with pytest.raises(ValueError, match='staged file hash mismatch'):
        amendment.validate_payload(manifest, staged)
    staged, manifest = payload(tmp_path / 'b')
    manifest['changed']['solver/unreviewed.py'] = {'old_sha256': None, 'new_sha256': 'x'}
    with pytest.raises(ValueError, match='four-file scope'):
        amendment.validate_payload(manifest, staged)


def test_the_staged_agentrepair_is_the_frozen_file_plus_one_hunk():
    old = (stage.FROZEN / 'eval/agentrepair.py').read_text(encoding='utf-8')
    new = stage.staged_agentrepair()
    assert old.count(stage.OLD_BLOCK) == 1 and new.count('key=optimizer_key, resolved=resolved') == 1
    assert new.replace(stage.NEW_BLOCK, stage.OLD_BLOCK) == old
