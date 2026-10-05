"""Revising binary or compiler inputs must not reuse stale investigation work."""

from solver import evidence_schedule, investigation
from eval import completion_campaign as campaign


def test_binary_input_revision_reopens_advanced_investigation():
    node = {'status': 'pending', 'source_sha256': 'same-source',
            'residual': {'compiled': True, 'frontend': {'passed': True}},
            'semantic_validation': {'source_sha256': 'same-source', 'status': 'unavailable'},
            'jobs': []}
    policy = investigation.policy()
    first = investigation.profile(node, 3, campaign.PROFILES, policy, 'old-binary-inputs')
    assert first['name'] == 'investigate'
    node['jobs'].append({'profile': 'investigate', 'evidence_key': first['evidence_key'],
                         'investigation_revision': first['investigation_revision']})
    same = investigation.profile(node, 3, campaign.PROFILES, policy, 'old-binary-inputs')
    revised = investigation.profile(node, 3, campaign.PROFILES, policy, 'new-binary-inputs')
    assert same is None or same['name'] != 'investigate'
    assert revised['name'] == 'investigate'
    assert revised['investigation_revision'] != first['investigation_revision']


def test_policy_only_investigation_keeps_historical_revision():
    node = {'status': 'pending', 'source_sha256': 'same-source',
            'residual': {'compiled': True, 'frontend': {'passed': True}},
            'semantic_validation': {'source_sha256': 'same-source', 'status': 'unavailable'},
            'jobs': []}
    policy = investigation.policy()
    selected = investigation.profile(node, 3, campaign.PROFILES, policy)
    assert selected['investigation_revision'] == evidence_schedule.fingerprint(policy)


def test_compiler_identity_hashes_recipe_and_native_sibling_binaries(tmp_path):
    compiler = tmp_path / 'tools' / 'ido-recomp' / 'linux'
    compiler.mkdir(parents=True)
    (compiler / 'cc').write_bytes(b'compiler launcher')
    (compiler / 'uopt').write_bytes(b'optimizer v1')
    recipe = {'target': 'build/src/f.o', 'command': ['tools/ido-recomp/linux/cc']}
    first = investigation.compiler_identity(tmp_path, recipe)
    assert first == investigation.compiler_identity(tmp_path, recipe)

    (compiler / 'uopt').write_bytes(b'optimizer v2')
    assert investigation.compiler_identity(tmp_path, recipe) != first
    (compiler / 'uopt').write_bytes(b'optimizer v1')
    assert investigation.compiler_identity(tmp_path, {**recipe, 'target': 'build/src/g.o'}) != first
    (compiler / 'uopt').unlink()
    assert investigation.compiler_identity(tmp_path, recipe) != first
