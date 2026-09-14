"""Evaluation-only intake checkpoints, including failures before compilation.

No imported function state or model hypothesis is promoted into binary evidence.
A changed input fingerprint makes parked intake failures eligible again.
"""
import json
import time


def ensure(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS eval_intake_outcomes (
        function TEXT NOT NULL, input_revision TEXT NOT NULL,
        status TEXT NOT NULL, receipt TEXT NOT NULL, detail TEXT NOT NULL,
        updated_at INTEGER NOT NULL, PRIMARY KEY(function, input_revision))''')
    conn.commit()


def parked(conn, revision):
    ensure(conn)
    return {r[0] for r in conn.execute(
        'SELECT function FROM eval_intake_outcomes WHERE input_revision=?', (revision,))}


def record(conn, revision, receipt, rows):
    ensure(conn)
    for row in rows:
        if not row.get('status') or row['status'] == 'running':
            continue
        conn.execute('''INSERT OR REPLACE INTO eval_intake_outcomes
            (function,input_revision,status,receipt,detail,updated_at) VALUES (?,?,?,?,?,?)''',
            (row['function'], revision, row['status'], str(receipt),
             json.dumps({k: row.get(k) for k in ('error', 'best_attempt_id', 'exact')}),
             int(time.time())))
    conn.commit()
