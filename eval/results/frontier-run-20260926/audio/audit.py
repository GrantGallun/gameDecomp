"""Read-only native checkpoint/ledger audit for the audioThreadMain reproduction."""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path('/mnt/c/Code/gameDecomp')
sys.path.insert(0, str(ROOT))
from eval.campaign_state import read

RUN = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
state = read(RUN / 'campaign.json')
node = state['nodes']['audioThreadMain']
print('state keys', sorted(state))
print('node', {k: node[k] for k in ('status', 'address', 'attempt_id', 'score', 'verification')})
print('frontier', [{k: f.get(k) for k in ('attempt_id', 'source_sha256', 'score', 'compiled')}
                   for f in node.get('frontier', [])])
conn = sqlite3.connect(f'file:{RUN / "campaign.sqlite"}?mode=ro', uri=True)
print('attempt columns', [r[1] for r in conn.execute('pragma table_info(attempts)')])
print('audio attempts', conn.execute('select count(*) from attempts where func_addr=?', (node['address'],)).fetchone()[0])
print('retained attempt', conn.execute('select id,source_sha256,compiled,score,exact,strategy,run_id,parent_attempt_id from attempts where id=?', (node['attempt_id'],)).fetchone())
old = Path('/home/grant/decomp/experiments/operand-repair-20260925/rows/audioThreadMain.json')
if old.exists():
    row = json.loads(old.read_text())
    print('prior experiment metadata', json.dumps({k: v for k, v in row.items() if k != 'source'}, indent=2))
