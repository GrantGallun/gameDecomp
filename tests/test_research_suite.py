"""Experiment identities and accounting must survive failed or changed inputs."""
import importlib
import json
from pathlib import Path

import pytest


def module(name):
    try:
        return importlib.import_module('eval.research_suite.' + name)
    except ModuleNotFoundError:
        pytest.fail('research experiment module is not implemented: ' + name)


def task_files(tmp_path):
    (tmp_path / 'root.c').write_text('unsigned int f(unsigned int x) { return x + 1; }\n')
    (tmp_path / 'target.o').write_bytes(b'fixture-object')
    return {'tasks': [{'id': 'one', 'function': 'f', 'source': str(tmp_path / 'root.c'),
                      'target_object': str(tmp_path / 'target.o'),
                      'compile_target': 'build/src/probe.o', 'assistance': 'synthetic'}]}


def test_frozen_bundle_survives_original_drift_but_rejects_bundle_tampering(tmp_path):
    m = module('manifest')
    config = task_files(tmp_path)
    path = tmp_path / 'frozen'
    m.freeze(config, path, identity={'compiler': 'fixture'})
    (tmp_path / 'root.c').write_text('changed')
    manifest = m.load(path)
    assert m.task_source(path, manifest['tasks'][0]).startswith('unsigned int f(')
    (path / manifest['tasks'][0]['source']).write_text('tampered')
    with pytest.raises(ValueError, match='hash'):
        m.load(path)


def test_freeze_refuses_overwrite_and_duplicate_tasks(tmp_path):
    m = module('manifest')
    config = task_files(tmp_path)
    config['tasks'] *= 2
    with pytest.raises(ValueError, match='duplicate'):
        m.freeze(config, tmp_path / 'bad', identity={})
    config['tasks'] = config['tasks'][:1]
    m.freeze(config, tmp_path / 'good', identity={})
    with pytest.raises(FileExistsError):
        m.freeze(config, tmp_path / 'good', identity={})


def test_certificate_acceptance_requires_source_target_and_frontend_identity():
    c = module('compiler')
    source = 'int f(void) { return 0; }'
    sha = module('manifest').digest(source.encode())
    cert = {'exact': True, 'kind': 'mips_object_section_certificate', 'schema_version': 1,
            'status': 'object_sections_exact', 'source_sha256': sha,
            'target_sha256': 'a' * 64, 'candidate_sha256': 'b' * 64}
    frontend = {'passed': True, 'source_sha256': sha}
    assert c.accepted(cert, frontend, source, 'a' * 64)
    assert not c.accepted(cert, {**frontend, 'passed': None}, source, 'a' * 64)
    assert not c.accepted(cert, frontend, source + '\n', 'a' * 64)
    assert not c.accepted(cert, frontend, source, 'c' * 64)
    assert not c.accepted({**cert, 'status': 'unverified'}, frontend, source, 'a' * 64)


def test_key_audit_distinguishes_collision_unavailable_and_different_key():
    k = module('key_audit')
    from solver.regalloc_search import Compiled
    calls = []

    def compile_source(source, label, parent):
        calls.append(source)
        return Compiled(source != 'fail', False, obj=source.encode() if source != 'fail' else None)

    keys = {'root': 'same', 'collision': 'same', 'fail': 'same', 'different': 'other'}
    r = k.audit('root', [('collision', 'collision'), ('fail', 'fail'), ('different', 'different')],
                compile_source, keys.get, lambda a, b: False if b.obj == b'collision' else None,
                budget=4)
    assert r['compiles'] == 4 == len(calls)
    assert r['same_key_pairs'] == 2
    assert r['conclusive'] == 1 and r['violations'] == 1 and r['unavailable'] == 1
    assert r['different_key_pairs'] == 1


def test_key_stress_variants_do_not_rewrite_string_or_comment_contents():
    k = module('key_audit')
    source = 'int f(void) { /* ; protected */ return "a;b"[0]; }\n'
    variants = list(k.layout_variants(source))
    assert variants
    assert all('"a;b"' in s and '/* ; protected */' in s for _, s in variants)


def test_strict_reconstruction_rejects_missing_or_truncated_diff():
    m = module('metrics')
    target = 'a\nb\nc\n'
    diff = '--- target\n+++ candidate\n@@ -1,3 +1,3 @@\n a\n-b\n+x\n c\n'
    assert m.reconstruct(target, diff) == 'a\nx\nc\n'
    with pytest.raises(ValueError):
        m.reconstruct(target, '')
    with pytest.raises(ValueError):
        m.reconstruct(target, diff.rsplit(' c', 1)[0])


def test_reconstruct_refuses_ambiguous_newline_identity():
    m = module('metrics')
    with pytest.raises(ValueError, match='newline'):
        m.reconstruct('a\n', '@@ -1 +1 @@\n-a\n+b')
    with pytest.raises(ValueError, match='newline'):
        m.reconstruct('a\n', '@@ -1 +1 @@\n-a\n+b\n\\ No newline at end of file\n')


def test_key_stress_preserves_macro_statement_topology():
    source = '#define F(x) x++; x++\nint f(int x) { F(x); return x; }\n'
    variants = module('key_audit').layout_variants(source)
    assert all('#define F(x) x++; x++\n' in value for _, value in variants)


def test_object_binding_rejects_wrong_function_even_when_sections_could_match():
    c = module('compiler')
    assert c.symbol_identity('f T 0 10\n', 'f')['status'] == 'symbol_verified'
    with pytest.raises(ValueError, match='requested isolated function'):
        c.symbol_identity('g T 0 10\n', 'f')
    with pytest.raises(ValueError):
        c.symbol_identity('f T 4 10\n', 'f')


def test_object_binding_accepts_the_projects_non_matching_marker_but_nothing_else():
    # `mips-linux-gnu-nm -g --defined-only -P` on sbk1 nonmatchings/*/target.o:
    c = module('compiler')
    nm = 'f T 0 114\nf.NON_MATCHING T 0 114\n'
    assert c.symbol_identity(nm, 'f')['status'] == 'symbol_verified'
    for bad in ('f T 0 114\ng.NON_MATCHING T 0 114\n',       # another function's marker
                'f T 0 114\nf.NON_MATCHING T 8 114\n',       # the marker elsewhere
                'f.NON_MATCHING T 0 114\n',                  # the marker without the function
                'f T 0 114\nf.NON_MATCHING T 0 114\ng T 4 8\n'):
        with pytest.raises(ValueError, match='requested isolated function'):
            c.symbol_identity(bad, 'f')


def test_failed_key_request_is_counted_and_logged(tmp_path, monkeypatch):
    c = module('compiler')
    compiler = c.NativeCompiler.__new__(c.NativeCompiler)
    compiler.output = tmp_path
    compiler.key_calls = 0
    compiler.key_seconds = 0.0

    def refuse(source):
        raise ValueError('deliberate admission refusal')

    monkeypatch.setattr(c, 'admit_source', refuse)
    with pytest.raises(ValueError, match='deliberate'):
        compiler.key('bad')
    assert compiler.key_calls == 1
    row = json.loads((tmp_path / 'keys.jsonl').read_text())
    assert row['key'] is None and 'deliberate' in row['error']
    assert row['seconds'] >= 0
