"""Pure payload checks; never accesses the live campaign."""
from pathlib import Path

import pytest

import apply
import stage


def _payload(tmp_path: Path) -> tuple[dict, Path]:
    staged = tmp_path / 'staged'
    changed = {}
    for rel in stage.CHANGED:
        file = staged / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(rel)
        changed[rel] = {'old_sha256': None if rel in stage.NEW else 'old-code',
                        'new_sha256': stage.sha(file),
                        'old_launch_sha256': None if rel in stage.NEW else 'old-launch'}
    tests, fixtures = {}, {}
    for roster, paths in ((tests, stage.TESTS), (fixtures, stage.FIXTURES)):
        for rel in paths:
            file = staged / rel
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(rel)
            roster[rel] = stage.sha(file)
    return ({'kind': 'unapplied-combined-frontier-branch-defaults',
             'changed': changed, 'tests': tests, 'test_only_fixtures': fixtures,
             'old_pin_digest': stage.EXPECTED_OLD_PIN_DIGEST,
             'old_pin_count': stage.EXPECTED_OLD_PIN_COUNT,
             'source_commit': stage.EXPECTED_COMMIT,
             'state_path': str(stage.STATE), 'frozen_project': str(stage.FROZEN)}, staged)


def test_payload_accepts_only_reviewed_three_file_scope(tmp_path):
    manifest, staged = _payload(tmp_path)
    apply.validate_payload(manifest, staged)


def test_payload_rejects_new_module_that_claims_an_old_file(tmp_path):
    manifest, staged = _payload(tmp_path)
    manifest['changed']['solver/branch_defaults.py']['old_sha256'] = 'unexpected'
    with pytest.raises(ValueError, match='invalid changed-file row'):
        apply.validate_payload(manifest, staged)


def test_payload_rejects_source_or_test_drift_and_extra_file(tmp_path):
    manifest, staged = _payload(tmp_path)
    (staged / 'solver/regalloc_mutations.py').write_text('drift')
    with pytest.raises(ValueError, match='staged source differs'):
        apply.validate_payload(manifest, staged)
    manifest, staged = _payload(tmp_path)
    (staged / stage.TESTS[0]).write_text('drift')
    with pytest.raises(ValueError, match='staged tests differs'):
        apply.validate_payload(manifest, staged)
    manifest, staged = _payload(tmp_path)
    manifest['changed']['solver/extra.py'] = {'old_sha256': None,
                                              'new_sha256': 'x', 'old_launch_sha256': None}
    with pytest.raises(ValueError, match='stage scope'):
        apply.validate_payload(manifest, staged)


def test_payload_rejects_other_checkpoint_identity(tmp_path):
    manifest, staged = _payload(tmp_path)
    manifest['source_commit'] += 1
    with pytest.raises(ValueError, match='another campaign boundary'):
        apply.validate_payload(manifest, staged)
