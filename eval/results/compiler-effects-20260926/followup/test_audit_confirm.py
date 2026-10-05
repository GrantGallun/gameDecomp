import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location('effect_audit_confirm',
                                             Path(__file__).with_name('audit_confirm.py'))
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def fixture_receipt():
    db = sqlite3.connect(':memory:')
    db.executescript('''
        CREATE TABLE attempts (id INTEGER, parent_attempt_id INTEGER, source_sha256 TEXT,
          compiled INTEGER, score REAL, exact INTEGER, compiler_stderr TEXT, sampling TEXT);
        CREATE TABLE attempt_edges (child_attempt_id INTEGER, parent_attempt_id INTEGER,
          relation TEXT, action TEXT);
    ''')
    sampling = {'training_eligible': False, 'header_assisted': True,
                'followup_parent_sha256': 'parent', 'frozen_source_sha256': 'child',
                'followup_manifest_sha256': 'manifest', 'frontend': {'passed': True},
                'verification': {'exact': True}}
    db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?)',
               (18, 17, 'child', 1, 100., 1, '', json.dumps(sampling)))
    db.execute('INSERT INTO attempt_edges VALUES (?,?,?,?)',
               (18, 17, 'prospective-second-edit', 'pure_inline:temp'))
    parent = {'parent_receipt_id': 17, 'parent_sha256': 'parent'}
    proposal = {'source_sha256': 'child', 'label': 'pure_inline:temp'}
    row = {'receipt_id': 18, 'compiled': True, 'score': 100., 'exact': True,
           'frontend_passed': True, 'certificate_exact': True, 'error': ''}
    return db, row, proposal, parent


def test_audits_actual_parent_edge_and_receipt():
    db, row, proposal, parent = fixture_receipt()
    result = audit.audited_attempt(row, proposal, parent, db, 'manifest')
    assert result['exact'] is True
    db.execute('UPDATE attempt_edges SET parent_attempt_id=99 WHERE child_attempt_id=18')
    with pytest.raises(ValueError, match='parent edge'):
        audit.audited_attempt(row, proposal, parent, db, 'manifest')


def test_draft_reports_actual_two_edit_labels():
    steps = [{'function': 'MusAsk', 'baseline_score': 98.5,
              'baseline_gradient': [2, 3, 4],
              'first_edit': {'label': 'pure_inline:temp', 'family': 'pure_inline',
                             'receipt_id': 17, 'score': 99.1, 'gradient': [1, 2, 3]},
              'second_edit': {'label': 'stmt_move:1->2', 'family': 'stmt_move',
                              'receipt_id': 18, 'score': 100., 'gradient': [0, 0, 0]}}]
    confirmed = {'function': 'MusAsk', 'confirmed': True, 'confirmation_receipt_id': 19,
                 'source_sha256': 'abc', 'actual_parent_receipt_id': 17,
                 'dedup': {'classification': 'new_vs_raw_exact_ledgers',
                           'native_campaign': [], 'research': []}}
    text = audit.draft_markdown({'attempt_count': 256, 'function_count': 8,
                                 'compiled_count': 250, 'frontend_pass_count': 220,
                                 'failure_count': 6, 'exact_count': 2},
                                steps, confirmed,
                                {'wall_seconds_sum': 300., 'elapsed_wall_seconds': 120.})
    assert '`pure_inline:temp`' in text
    assert '`stmt_move:1->2`' in text
    assert 'no compiler trace was taken' in text
