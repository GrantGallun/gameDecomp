"""Snapshot candidate-only inputs; never mutate the running campaign."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path('/mnt/c/Code/gameDecomp')
NATIVE = Path('/home/grant/decomp/experiments/investigation-loop-20260927')
LIVE = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
sys.path.insert(0, str(ROOT))
from eval import campaign_state, campaign_workers
from solver import repair_queue

NATIVE.mkdir(parents=True, exist_ok=True)
state = campaign_state.read(LIVE / 'campaign.json')
pointer = json.loads((LIVE / 'campaign.json').read_text())
excluded = set()
for path in (LIVE / 'campaign.sqlite', Path('/home/grant/decomp/kb-sbk1.sqlite')):
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as conn:
        excluded.update(r[0] for r in conn.execute('SELECT DISTINCT f.name FROM attempts a '
            'JOIN functions f ON f.addr=a.func_addr WHERE a.exact=1'))
selected = []
for lane in ('byte', 'semantic', 'environment', 'frontend'):
    eligible = []
    for name, node in state['nodes'].items():
        if name in excluded or not node.get('source') or node['status'] != 'pending':
            continue
        if repair_queue.lane(node).value != lane or not node.get('jobs'):
            continue
        path = Path(node['source'])
        if not path.is_file() or not 1 <= path.stat().st_size <= 24000:
            continue
        if (node.get('instruction_count') or 9999) > 160:
            continue
        eligible.append((node.get('instruction_count') or 9999, name, node))
    for _, name, node in sorted(eligible)[:2]:
        source = Path(node['source']).read_text()
        if hashlib.sha256(source.encode()).hexdigest() != node['source_sha256']:
            raise ValueError('candidate source changed: ' + name)
        selected.append({'function': name, 'lane': lane, 'node': node})

manifest = {'checkpoint': pointer['commit'], 'regime': 'header-assisted development candidates; no held-out source',
            'selected': selected, 'excluded_prior_exact_count': len(excluded), 'source_config': state['config'],
            'shared_issues': repair_queue.shared_issues(state['nodes'])}
dest = NATIVE / 'manifest.json'
if dest.exists():
    raise RuntimeError('canary manifest already exists')
dest.write_text(json.dumps(manifest, indent=2) + '\n')
campaign_workers.synchronize(LIVE / 'campaign.sqlite', NATIVE / 'canary.sqlite')
print(json.dumps({'native': str(NATIVE), 'checkpoint': pointer['commit'],
    'selected': [{'function': r['function'], 'lane': r['lane'],
                  'score': r['node'].get('score'), 'instructions': r['node'].get('instruction_count')}
                 for r in selected]}, indent=2), flush=True)
