import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location('direct_compiler_harness', Path(__file__).with_name('harness.py'))
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def test_manifest_freezes_distinct_sources_before_any_compile(tmp_path):
    manifest = {'repo': '/home/grant/decomp/sbk1', 'kb': '/home/grant/decomp/kb.sqlite',
                'native_db': '/home/grant/decomp/run/campaign.sqlite', 'function': 'example',
                'addr': 123, 'native_attempt_id': 4, 'source_sha256': sha('parent'),
                'expected_baseline_score': 99.889,
                'proposals': [{'label': 'swap', 'source': 'child', 'source_sha256': sha('child')}]}
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(manifest))
    assert harness.read_manifest(path)['proposals'][0]['label'] == 'swap'
    manifest['proposals'][0]['source_sha256'] = sha('other')
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='proposal source hash'):
        harness.read_manifest(path)
    manifest['proposals'][0]['source_sha256'] = sha('child')
    manifest['proposals'].append(dict(manifest['proposals'][0]))
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='duplicate'):
        harness.read_manifest(path)


def test_direct_comparison_binds_hashes_and_object_evidence():
    parent = {'source_sha256': 'a', 'receipt_id': 4, 'score': 99.0, 'compiled': True, 'exact': False,
              'frontend_passed': True, 'certificate_exact': False, 'asm': 'one\n', 'diff': 'old'}
    child = {'label': 'swap', 'source_sha256': 'b', 'receipt_id': 5, 'score': 100.0, 'compiled': True,
             'exact': True, 'frontend_passed': True, 'certificate_exact': True,
             'asm': 'two\n', 'diff': '', 'elapsed_seconds': 0.5}
    report = harness.compare(parent, child)
    assert report['parent_source_sha256'] == 'a'
    assert report['child_source_sha256'] == 'b'
    assert report['score_delta'] == 1.0
    assert report['object_exact'] is True
    assert report['assembly_equal'] is False
    child['frontend_passed'] = False
    assert harness.compare(parent, child)['object_exact'] is False


def test_artifacts_preserve_raw_c_and_s_even_without_certificate(tmp_path):
    receipt = {'source_sha256': sha('void example() {}'), 'receipt_id': 8,
               'asm': 'jr ra\n', 'diff': '@@ -1 +1 @@\n', 'frontend_passed': None,
               'verification': None, 'error': ''}
    harness.save_artifacts(tmp_path / 'artifacts', 'child', 'void example() {}',
                           receipt)
    assert (tmp_path / 'artifacts' / 'child.c').read_text() == 'void example() {}'
    assert (tmp_path / 'artifacts' / 'child.s').read_text() == 'jr ra\n'
    assert (tmp_path / 'artifacts' / 'child.diff').read_text() == '@@ -1 +1 @@\n'
    assert json.loads((tmp_path / 'artifacts' / 'child.certificate.json').read_text())['verification'] is None


def test_direct_scorer_uses_direct_labels_and_keeps_verification(monkeypatch, tmp_path):
    called = {}

    def fake_score(ws, repo, tag, source, **kwargs):
        called.update(kwargs)
        return harness.workspace.Attempt(True, 99.9, False, 'diff', '', '',
                                         verification={'exact': False}, receipt_id=9)

    monkeypatch.setattr(harness.workspace, 'score', fake_score)
    result = harness.score_one(object(), tmp_path, tmp_path,
                               {'function': 'example', 'native_attempt_id': 4},
                               'void example() {}', 'example_direct_swap', 8, 'swap')
    assert called['strategy'] == 'direct-compiler:swap'
    assert called['model'] == 'deterministic'
    assert called['run_id'] == 'direct-compiler-20260926:example'
    assert called['parent_attempt_id'] == 8
    assert result['verification'] == {'exact': False}
    assert result['elapsed_seconds'] >= 0
