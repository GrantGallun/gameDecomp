"""Small representation/scheduling probes on the current object mismatch frontier."""
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
baseline = {r['function']: r for r in json.loads((OUT / 'baseline-reviewed.json').read_text())['rows']}
units = json.loads((OUT / 'units-probe.json').read_text())
conn = sqlite3.connect(NATIVE / 'attempts.sqlite')
rows = []
for name in ('updateRaceCoursePropModels', 'releaseMenuAssetHandles', 'allocTranslationOnlyFixedMatrix'):
    if name == 'allocTranslationOnlyFixedMatrix':
        source = baseline[name]['source']
        before = '    (*(s32 *)((unsigned char *)temp_v0 + 0x20)) = 0;\n'
        field = '    (*(s32 *)((unsigned char *)temp_v0 + 0x1C)) = (s32) ((arg0->unk1C & 0xFFFF0000) | 1);\n'
        # Diff shows a load moved across these two stores, not a changed field.
        variants = [('swap-adjacent-stores', source.replace(before + field, field + before))]
    else:
        source = next(r['source'] for r in units if r['function'] == name)
        typ, local = ('s16', 'temp_t0') if name.startswith('update') else ('s16', 'temp_a0')
        variants = [('widen-loaded-halfword-local', source.replace(f'{typ} {local};', f's32 {local};')),
                    ('register-loaded-halfword-local', source.replace(f'{typ} {local};', f'register {typ} {local};')),
                    ('register-wide-loaded-halfword-local', source.replace(f'{typ} {local};', f'register s32 {local};'))]
    native = isolate(REPO, NATIVE / 'representation-probe-builds' / name, name)
    ws = native / 'nonmatchings' / name
    parent_sha = hashlib.sha256(source.encode()).hexdigest()
    parent = conn.execute('SELECT id FROM attempts WHERE source_sha256=? ORDER BY id DESC LIMIT 1', (parent_sha,)).fetchone()[0]
    for label, child in variants:
        att = workspace.score(ws, native, name, child, conn=conn, func=name,
            strategy='clean-composition:representation-probe', run_id='clean-composition-20260922:representation-probe',
            model='zero-model', parent_attempt_id=parent, action=label, relation='repair')
        front = frontend_diagnostics.analyse(child, repo=native, target=baseline[name]['target'], full_diagnostics=True)
        row = dict(function=name, label=label, source=child, source_sha256=hashlib.sha256(child.encode()).hexdigest(),
                   verdict=_attempt_to_verdict(att), frontend=front)
        rows.append(row)
        print(json.dumps(dict(function=name, label=label, score=att.score, exact=att.exact, frontend=front['status'])), flush=True)
        (OUT / 'representation-probe.json').write_text(json.dumps(rows, indent=2) + '\n')
