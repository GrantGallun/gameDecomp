"""Pure deployment guards; never access the live campaign state."""
from pathlib import Path

import pytest

import apply
import stage


def payload(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(stage, 'expected_branch', lambda: {'new_pin_digest': 'pin', 'commit': 7})
    monkeypatch.setattr(stage, 'sha', lambda path: 'payload' if path == stage.HERE / 'manifest.json'
                        else original_sha(path))
    staged = tmp_path / 'staged'
    changed = {}
    for rel in stage.CHANGED:
        file = staged / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(rel)
        changed[rel] = {'old_sha256': None if rel in stage.NEW else 'old',
                        'new_sha256': original_sha(file),
                        'old_launch_sha256': None if rel in stage.NEW else 'launch'}
    tests = {}
    for rel in stage.TESTS:
        file = staged / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(rel)
        tests[rel] = original_sha(file)
    return ({'kind': 'unapplied-combined-frontier-frontend',
             'changed': changed, 'tests': tests,
             'frozen_test_sha256': original_sha(stage.FROZEN / stage.FROZEN_TEST),
             'old_pin_digest': 'pin', 'old_pin_count': stage.EXPECTED_PIN_COUNT,
             'source_commit': 7, 'payload_manifest_sha256': 'payload',
             'state_path': str(stage.STATE), 'frozen_project': str(stage.FROZEN)}, staged)


original_sha = stage.sha


def test_accepts_exact_twelve_file_scope(tmp_path, monkeypatch):
    manifest, staged = payload(tmp_path, monkeypatch)
    apply.validate_payload(manifest, staged)


def test_existing_pinned_file_can_be_absent_from_archived_launch(tmp_path, monkeypatch):
    manifest, staged = payload(tmp_path, monkeypatch)
    manifest['changed']['solver/frontend_fixits.py']['old_launch_sha256'] = None
    apply.validate_payload(manifest, staged)


def test_rejects_substituted_file_and_drift(tmp_path, monkeypatch):
    manifest, staged = payload(tmp_path, monkeypatch)
    manifest['changed']['solver/unreviewed.py'] = manifest['changed'].pop(stage.CHANGED[0])
    with pytest.raises(ValueError, match='scope'):
        apply.validate_payload(manifest, staged)
    manifest, staged = payload(tmp_path, monkeypatch)
    (staged / stage.CHANGED[0]).write_text('drift')
    with pytest.raises(ValueError, match='staged source differs'):
        apply.validate_payload(manifest, staged)


def test_rejects_false_new_module_and_changed_boundary(tmp_path, monkeypatch):
    manifest, staged = payload(tmp_path, monkeypatch)
    manifest['changed'][stage.CHANGED[0]]['old_sha256'] = 'already installed'
    with pytest.raises(ValueError, match='invalid changed-file row'):
        apply.validate_payload(manifest, staged)
    manifest, staged = payload(tmp_path, monkeypatch)
    manifest['source_commit'] += 1
    with pytest.raises(ValueError, match='another campaign boundary'):
        apply.validate_payload(manifest, staged)
