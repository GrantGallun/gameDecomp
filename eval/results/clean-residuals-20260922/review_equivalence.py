"""Bind post-review guards to every candidate actually compiled in the paired replay."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval.intake_runners import RUNNERS

OUT = Path(__file__).resolve().parent
NATIVE = Path.home() / 'decomp/experiments/clean-residuals-20260922'
paired = json.loads((OUT / 'paired.json').read_text())
targets = {r['function']: r['target'] for r in json.loads((OUT / 'census.json').read_text())['rows']}
rows, differences = [], []
for row in paired['rows']:
    name = row['function']
    repo = NATIVE / 'paired-builds' / name
    ws = repo / 'nonmatchings' / name
    source = (OUT / 'states' / name / 'before.c').read_text()
    verdict = dict(row['before'])
    steps = []
    for step in row['trace']:
        result = RUNNERS[step['action']](dict(function=name, candidate=source, repo=str(repo),
            target=targets[name], workspace=str(ws), target_asm_path=str(ws / 'target.s'), initial_verdict=verdict), {})
        digest = hashlib.sha256(result.get('source', source).encode()).hexdigest()
        same = bool(result.get('changed')) == step['changed'] and (not step['changed'] or digest == step['source_sha256'])
        steps.append(dict(action=step['action'], equivalent=same, source_sha256=digest))
        if not same:
            differences.append(dict(function=name, action=step['action']))
            break
        if step.get('adopted'):
            source = result['source']
            verdict.update(compiled=step['compiled'], exact=step['exact'])
    final_same = hashlib.sha256(source.encode()).hexdigest() == row['after']['source_sha256']
    rows.append(dict(function=name, steps=steps, final_equivalent=final_same))
    if len(rows) % 50 == 0:
        print(f'Checked {len(rows)}/200; differences={len(differences)}', flush=True)
report = dict(states=len(rows), differences=differences, all_equivalent=not differences and all(r['final_equivalent'] for r in rows),
    code_sha256={f:hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in (
        'solver/void_field_repair.py','solver/frontend_fixits.py','eval/intake_runners.py','eval/intake_probe.py')}, rows=rows)
(OUT / 'review-equivalence.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k!='rows'},indent=2))
