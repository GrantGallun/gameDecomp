"""Measure the returned-local scanner correction against its archived implementation."""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval import campaign_workers
from eval.intake_runners import RUNNERS
from eval.intake_probe import rank
from eval.tool_agent_run import _attempt_to_verdict
from solver import frontend_diagnostics, void_field_repair, workspace

OUT = Path(__file__).resolve().parent
NATIVE = Path.home() / 'decomp/experiments/clean-residuals-20260922'
NATIVE.mkdir(parents=True, exist_ok=True)
conn = sqlite3.connect(NATIVE / 'probe-attempts.sqlite')
conn.executescript((ROOT / 'kb/schema.sql').read_text())
prior = sqlite3.connect(f"file:{Path.home() / 'decomp/kb-sbk1.sqlite'}?mode=ro", uri=True)
for table in ('tus', 'functions'):
    columns = [r[1] for r in conn.execute(f'PRAGMA table_info({table})')]
    conn.executemany(f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                    prior.execute(f"SELECT {','.join(columns)} FROM {table}").fetchall())
conn.commit()
namespace = dict(void_field_repair.__dict__)
exec((OUT / 'void-field-before.py.txt').read_text(), namespace)
old = namespace['propose']
rows = []
for entry in json.loads((OUT / 'census.json').read_text())['rows']:
    name = entry['function']
    folder = OUT / 'states' / name
    source = (folder / 'before.c').read_text()
    front = json.loads((folder / 'before-frontend.json').read_text())
    asm = (Path.home() / 'decomp/experiments/clean-members-20260922/final-builds' / name / 'nonmatchings' / name / 'target.s').read_text()
    before = old(source, name, asm, front['diagnostics'])
    proposal = void_field_repair.propose(source, name, asm, front['diagnostics'])
    if proposal['source'] == before['source']:
        continue
    repo = campaign_workers.isolate(Path.home() / 'decomp/sbk1', NATIVE / 'probe-builds' / name, name)
    ws = repo / 'nonmatchings' / name
    def score(code, label):
        return _attempt_to_verdict(workspace.score(ws, repo, name, code, conn=conn, func=name,
            strategy='clean-residuals:void-fix:'+label, model='zero-model', run_id='clean-residuals-void-fix'))
    baseline = score(source, 'baseline')
    best = score(proposal['source'], 'returned-local')
    candidate = proposal['source'] if rank(best) >= rank(baseline) else source
    if candidate == source:
        best = baseline
    trace = []
    for label in ('frontend_casts', 'ido_byte_cursors'):
        result = RUNNERS['eval.intake_runners.'+label](dict(candidate=candidate, function=name,
            repo=str(repo), target=entry['target'], workspace=str(ws), initial_verdict=best), {})
        if result.get('changed'):
            verdict = score(result['source'], label)
            trace.append(dict(action=label, compiled=verdict['compiled'], exact=verdict['exact']))
            if rank(verdict) >= rank(best):
                candidate, best = result['source'], verdict
    final = frontend_diagnostics.analyse(candidate, repo=repo, target=entry['target'])
    item = dict(function=name, old_changes=len(before['changes']), new_changes=len(proposal['changes']),
        before=dict(compiled=baseline['compiled'], exact=baseline['exact'], errors=front['error_count']),
        after=dict(compiled=best['compiled'], exact=best['exact'], score=best['score'], errors=final['error_count'], frontend=final['status']), trace=trace)
    rows.append(item)
    (folder / 'void-fix.c').write_text(candidate)
    print(json.dumps(item), flush=True)
(OUT / 'void-fix-probe.json').write_text(json.dumps(rows, indent=2)+'\n')
