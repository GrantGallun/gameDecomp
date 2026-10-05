"""Binary-diff-motivated source hypotheses; no target C bodies are read."""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval.campaign_workers import isolate
from eval.tool_agent_run import _attempt_to_verdict
from solver import workspace, frontend_diagnostics

OUT = Path(__file__).resolve().parent
NATIVE = Path.home() / 'decomp/experiments/clean-composition-20260922'
REPO = Path.home() / 'decomp/sbk1'
baseline = {r['function']: r for r in json.loads((OUT / 'baseline.json').read_text())['rows']}
conn = sqlite3.connect(NATIVE / 'attempts.sqlite')
rows = []
for name in ('releaseMenuAssetHandles', 'updateRaceCoursePropModels'):
    initial = baseline[name]
    source = (OUT / 'states' / name / 'object-after.c').read_text()
    if name == 'releaseMenuAssetHandles':
        changed = source.replace('&gAssetHandles + 0xE', '(unsigned char *)&gAssetHandles + 0xE')
        variants = [('byte-global-offset', changed),
                    ('byte-global-offset+compare', changed.replace('temp_a0 != -1', '-1 != temp_a0'))]
    else:
        changed = source.replace('*(&gRaceCoursePropModelLists + (arg0->unk10 * 4))',
                                  '*(s32 *)((unsigned char *)&gRaceCoursePropModelLists + (arg0->unk10 * 4))')
        changed = changed.replace('var_s1 + 4', '(unsigned char *)var_s1 + 4')
        variants = [('byte-offsets', changed),
            ('byte-offsets+first-compare', changed.replace('*var_s1 != -1', '-1 != *var_s1')),
            ('byte-offsets+loop-compare', changed.replace('temp_t0 != -1', '-1 != temp_t0')),
            ('byte-offsets+both-compares', changed.replace('*var_s1 != -1', '-1 != *var_s1').replace('temp_t0 != -1', '-1 != temp_t0'))]
    native = isolate(REPO, NATIVE / 'units-probe-builds' / name, name)
    ws = native / 'nonmatchings' / name
    parent_sha = hashlib.sha256(source.encode()).hexdigest()
    parent = conn.execute('SELECT id FROM attempts WHERE source_sha256=? ORDER BY id DESC LIMIT 1', (parent_sha,)).fetchone()[0]
    for label, child in variants:
        attempt = workspace.score(ws, native, name, child, conn=conn, func=name,
            strategy='clean-composition:units-probe', run_id='clean-composition-20260922:units-probe',
            model='zero-model', parent_attempt_id=parent, action=label, relation='repair')
        front = frontend_diagnostics.analyse(child, repo=native, target=initial['target'], full_diagnostics=True)
        row = dict(function=name, label=label, source=child, source_sha256=hashlib.sha256(child.encode()).hexdigest(),
                   verdict=_attempt_to_verdict(attempt), frontend=front)
        rows.append(row)
        print(json.dumps(dict(function=name, label=label, score=attempt.score, exact=attempt.exact, frontend=front['status'])), flush=True)
        (OUT / 'units-probe.json').write_text(json.dumps(rows, indent=2) + '\n')
