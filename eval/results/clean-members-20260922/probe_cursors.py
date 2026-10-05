"""Measure existing byte-cursor lowering on frontend-clean IDO failures."""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from solver import frontend_diagnostics, void_pointer_units, workspace

OUT = Path(__file__).resolve().parent
NATIVE = Path.home() / 'decomp/experiments/clean-members-20260922'
paired = json.loads((OUT / 'paired.json').read_text())
census = json.loads((OUT / 'census.json').read_text())
targets = {r['function']: r['target'] for r in census['rows']}
conn = sqlite3.connect(NATIVE / 'cursor-attempts.sqlite')
conn.executescript((ROOT / 'kb/schema.sql').read_text())
prior = sqlite3.connect(f"file:{paired['attempt_db']}?mode=ro", uri=True)
for table in ('tus', 'functions'):
    columns = [r[1] for r in conn.execute(f'PRAGMA table_info({table})')]
    conn.executemany(f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                     prior.execute(f"SELECT {','.join(columns)} FROM {table}").fetchall())
conn.commit()
rows = []
for row in paired['rows']:
    if row['after']['frontend'] != 'passed' or row['after']['compiled']:
        continue
    name = row['function']
    repo = NATIVE / 'paired-builds' / name
    ws = repo / 'nonmatchings' / name
    source = (OUT / 'states' / name / 'after.c').read_text()
    assert hashlib.sha256(source.encode()).hexdigest() == row['after']['source_sha256']
    child, report = void_pointer_units.propose(source, name, (ws / 'target.s').read_text())
    item = {'function': name, 'report': report, 'changed': child != source}
    # No default or inferred pseudo-fields enter this experiment.
    assert not report['fields'], item
    if child != source:
        attempt = workspace.score(ws, repo, name, child, conn=conn, func=name,
            strategy='clean-members:cursor-probe', model='zero-model', run_id='clean-members-cursor-20260922')
        front = frontend_diagnostics.analyse(child, repo=repo, target=targets[name])
        item.update(compiled=attempt.compiled, exact=attempt.exact, score=attempt.score,
                    frontend=front['status'], errors=front['error_count'])
        (OUT / 'states' / name / 'cursor-probe.c').write_text(child)
    rows.append(item)
    print(json.dumps(item), flush=True)
(OUT / 'cursor-probe.json').write_text(json.dumps(rows, indent=2) + '\n')
