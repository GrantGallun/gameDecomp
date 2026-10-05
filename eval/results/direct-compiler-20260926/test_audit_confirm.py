import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location('direct_compiler_audit', Path(__file__).with_name('audit_confirm.py'))
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def test_receipt_audit_checks_actual_edge_and_source_bytes():
    db = sqlite3.connect(':memory:')
    db.executescript('''
        CREATE TABLE attempts (id INTEGER, parent_attempt_id INTEGER, source_code TEXT,
          source_sha256 TEXT, compiled INTEGER, score REAL, exact INTEGER,
          strategy TEXT, model TEXT, run_id TEXT, sampling TEXT);
        CREATE TABLE attempt_edges (child_attempt_id INTEGER, parent_attempt_id INTEGER,
          relation TEXT, action TEXT);
    ''')
    source = 'void f() {}'
    digest = audit.harness.benchmark.digest(source)
    sampling = {'training_eligible': False, 'native_parent_attempt_id': 3,
                'frozen_source_sha256': digest, 'frontend': {'passed': True},
                'verification': {'exact': True}}
    db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?)',
               (5, 4, source, digest, 1, 100., 1, 'direct-compiler:win',
                'deterministic', 'direct-compiler-20260926:f', json.dumps(sampling)))
    db.execute('INSERT INTO attempt_edges VALUES (?,?,?,?)',
               (5, 4, 'direct-compiler-probe', 'win'))
    expected = {'receipt_id': 5, 'parent_id': 4, 'source_sha256': digest,
                'compiled': True, 'score': 100., 'exact': True,
                'frontend_passed': True, 'certificate_exact': True, 'action': 'win'}
    assert audit.audit_receipt(db, expected, 'f', 3)['receipt_id'] == 5
    db.execute('UPDATE attempt_edges SET parent_attempt_id=3')
    with pytest.raises(ValueError, match='edge'):
        audit.audit_receipt(db, expected, 'f', 3)


def test_metadata_exact_query_does_not_need_source_column(tmp_path):
    path = tmp_path / 'ledger.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE functions(addr INTEGER, name TEXT);
            CREATE TABLE attempts(id INTEGER,func_addr INTEGER,source_sha256 TEXT,compiled INTEGER,exact INTEGER);
            INSERT INTO functions VALUES(1,'f');
            INSERT INTO attempts VALUES(7,1,'digest',1,1);''')
    assert audit.exact_metadata(path, 'f') == [{'id': 7, 'source_sha256': 'digest', 'compiled': True}]


def test_nonexact_receipt_without_certificate_audits_as_none():
    db = sqlite3.connect(':memory:')
    db.executescript('''
        CREATE TABLE attempts (id INTEGER, parent_attempt_id INTEGER, source_code TEXT,
          source_sha256 TEXT, compiled INTEGER, score REAL, exact INTEGER,
          strategy TEXT, model TEXT, run_id TEXT, sampling TEXT);
        CREATE TABLE attempt_edges (child_attempt_id INTEGER, parent_attempt_id INTEGER,
          relation TEXT, action TEXT);
    ''')
    source = 'void f() {}'
    digest = audit.harness.benchmark.digest(source)
    sampling = {'training_eligible': False, 'native_parent_attempt_id': 3,
                'frozen_source_sha256': digest, 'frontend': {'passed': True}}
    db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?)',
               (5, 4, source, digest, 1, 99.9, 0, 'direct-compiler:miss',
                'deterministic', 'direct-compiler-20260926:f', json.dumps(sampling)))
    db.execute('INSERT INTO attempt_edges VALUES (?,?,?,?)',
               (5, 4, 'direct-compiler-probe', 'miss'))
    expected = {'receipt_id': 5, 'parent_id': 4, 'source_sha256': digest,
                'compiled': True, 'score': 99.9, 'exact': False,
                'frontend_passed': True, 'certificate_exact': None, 'action': 'miss'}
    assert audit.audit_receipt(db, expected, 'f', 3)['exact'] is False


def test_artifact_certificate_preserves_absent_verification(tmp_path):
    path = tmp_path / 'miss.certificate.json'
    path.write_text(json.dumps({'receipt_id': 5, 'source_sha256': 'digest',
                                'verification': None, 'frontend_passed': True}))
    assert audit.artifact_certificate(path, 5, 'digest', True) is None
    with pytest.raises(ValueError, match='artifact'):
        audit.artifact_certificate(path, 6, 'digest', True)
