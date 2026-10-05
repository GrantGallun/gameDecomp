"""Verify canonical imported attempts and acceptance from a private canary."""
import argparse
import json
from pathlib import Path
import sqlite3

HERE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--report', type=Path, default=HERE / 'controller-canary.json')
parser.add_argument('--require-nonidentity', action='store_true')
parser.add_argument('--require-log-receipt-ids', action='store_true')
args = parser.parse_args()
data = json.loads(args.report.read_text())
assert data['error'] is None and data['fast_inflight'] == 0
assert data['last_session']['completed_items'] == 2
assert data['imported']['new_attempts'] == data['imported']['new_edges'] == 18
assert not data['imported']['foreign_key_failures'] and data['imported']['new_model_attempts'] == 0
conn = sqlite3.connect(f'file:{data["private_db"]}?mode=ro', uri=True)
checks = {}
nonidentity = 0
for name, row in data['results'].items():
    assert row['after']['status'] == 'object_exact' and row['after']['score'] == 100.0
    assert row['frontend_passed'] and row['certificate_status'] == 'object_sections_exact'
    assert row['prior_job_history_preserved']
    assert len(row['new_jobs']) == 1
    job = row['new_jobs'][0]
    assert job['profile'].startswith('operand_repair@')
    mapping = {int(k): int(v) for k, v in job['private_lineage']['attempt_ids'].items()}
    nonidentity += sum(private_id != canonical_id for private_id, canonical_id in mapping.items())
    assert len(mapping) == job['proposal_compiles'] + 1
    assert row['after']['attempt_id'] in mapping.values()
    baseline = conn.execute('SELECT id,parent_attempt_id FROM attempts WHERE id IN '
                            f'({",".join("?" for _ in mapping)}) AND strategy LIKE "operand-repair:baseline:%"',
                            tuple(mapping.values())).fetchone()
    assert baseline and baseline[1] == row['before']['attempt_id']
    imported = conn.execute(
        f'SELECT a.id,a.parent_attempt_id,a.strategy,a.exact FROM attempts a WHERE a.id IN '
        f'({",".join("?" for _ in mapping)}) ORDER BY a.id', tuple(mapping.values())).fetchall()
    assert all(parent in {row['before']['attempt_id'], *(item[0] for item in imported)}
               for _id, parent, _strategy, _exact in imported)
    assert all(conn.execute('SELECT count(*) FROM attempt_edges WHERE parent_attempt_id=? AND child_attempt_id=?',
                            (parent, attempt_id)).fetchone()[0] == 1
               for attempt_id, parent, _strategy, _exact in imported)
    exact = [item for item in imported if item[3]]
    assert len(exact) == 1 and exact[0][0] == row['after']['attempt_id']
    assert job['private_lineage']['dispatch_profile']['name'] == job['profile']
    canonical = json.loads(Path(job['receipt']).read_text())
    assert (canonical.get('performance') or {}).get('model_calls') == 0
    compiler_logs = [entry for entry in canonical.get('log', []) if 'compiled' in entry]
    if args.require_log_receipt_ids:
        assert len(compiler_logs) == len(mapping)
        assert all('receipt_id' in entry and 'receipt' not in entry for entry in compiler_logs)
    for entry in compiler_logs:
        receipt_id = entry.get('receipt_id')
        if receipt_id is not None:
            assert receipt_id in mapping.values()
            assert conn.execute('SELECT count(*) FROM attempts WHERE id=?', (receipt_id,)).fetchone()[0] == 1
            parent_id = entry['parent_attempt_id']
            assert conn.execute('SELECT count(*) FROM attempt_edges WHERE parent_attempt_id=? AND child_attempt_id=?',
                                (parent_id, receipt_id)).fetchone()[0] == 1
    if name == 'releaseSoundEffectHandleNode':
        assert any(edit['complete'] and edit['label'] == 'local_web_merge:temp_v1+temp_v1_2'
                   for edit in job['local_web_merge'])
    checks[name] = {'imported_attempts': len(imported), 'baseline': baseline[0],
                    'original_parent': baseline[1], 'exact_attempt': exact[0][0],
                    'dispatch_profile': job['profile'],
                    'nonidentity_import_ids': sum(k != v for k, v in mapping.items()),
                    'canonical_log_receipts': len([entry for entry in compiler_logs if 'receipt_id' in entry])}
assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
if args.require_nonidentity:
    assert nonidentity > 0
print(json.dumps(checks, indent=2))
