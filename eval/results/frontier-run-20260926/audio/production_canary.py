"""Fresh private native canary for eval.operand_repair.run; no prior winner input."""
import gc
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path('/mnt/c/Code/gameDecomp')
sys.path.insert(0, str(ROOT))
from eval.campaign_state import read
from eval.campaign_workers import isolate
from eval import operand_repair

NAME = 'audioThreadMain'
RUN = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
REPO = Path('/home/grant/decomp/sbk1')
PRIVATE = Path('/home/grant/decomp/experiments/frontier-run-20260926/audio/production-canary-v2')
REPORT = ROOT / 'eval/results/frontier-run-20260926/audio/production-canary.json'

if PRIVATE.exists():
    raise RuntimeError('canary directory already exists; refusing to reuse it')
PRIVATE.mkdir(parents=True)
state = read(RUN / 'campaign.json')
full = state['nodes'][NAME]
node = {key: full.get(key) for key in ('status', 'address', 'attempt_id', 'score',
                                      'source_sha256', 'residual', 'verification',
                                      'semantic_validation')}
checkpoint = json.loads((RUN / 'campaign.json').read_text()).get('commit')
del full, state
gc.collect()

db = PRIVATE / 'attempts.sqlite'
conn = sqlite3.connect(db)
conn.executescript((ROOT / 'kb/schema.sql').read_text())
conn.execute('ATTACH DATABASE ? AS kb', ('file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro',))
function_row = conn.execute('SELECT * FROM kb.functions WHERE addr=?', (node['address'],)).fetchone()
conn.execute('INSERT INTO tus SELECT * FROM kb.tus WHERE id=?', (function_row[2],))
conn.execute('INSERT INTO functions SELECT * FROM kb.functions WHERE addr=?', (node['address'],))
conn.execute('ATTACH DATABASE ? AS native', (f'file:{RUN / "campaign.sqlite"}?mode=ro',))
ancestors = []
attempt_id = node['attempt_id']
while attempt_id is not None:
    row = conn.execute('SELECT id,parent_attempt_id,run_id FROM native.attempts WHERE id=?',
                       (attempt_id,)).fetchone()
    if row is None:
        raise RuntimeError(f'native ancestor {attempt_id} missing')
    ancestors.append(row)
    attempt_id = row[1]
for attempt_id, _, run_id in reversed(ancestors):
    if run_id:
        conn.execute('INSERT OR IGNORE INTO attempt_runs SELECT * FROM native.attempt_runs WHERE id=?', (run_id,))
    conn.execute('INSERT INTO attempts SELECT * FROM native.attempts WHERE id=?', (attempt_id,))
source, source_hash = conn.execute('SELECT source_code,source_sha256 FROM attempts WHERE id=?',
                                   (node['attempt_id'],)).fetchone()
conn.commit()
conn.close()
if hashlib.sha256(source.encode()).hexdigest() != source_hash or source_hash != node['source_sha256']:
    raise RuntimeError('native retained source identity mismatch')
source_path = PRIVATE / 'retained.c'
source_path.write_text(source)
node['source'] = str(source_path)
isolated = isolate(REPO, PRIVATE / 'isolate', NAME)
result = operand_repair.run(repo=isolated, db=db, function=NAME, node=node,
                            out=PRIVATE / 'audio.json', budget=72)
conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
attempts = conn.execute('SELECT count(*) FROM attempts').fetchone()[0]
edges = conn.execute('SELECT count(*) FROM attempt_edges').fetchone()[0]
exact_rows = conn.execute('SELECT id,source_sha256 FROM attempts WHERE exact=1').fetchall()
conn.close()
summary = {'checkpoint': checkpoint, 'native_attempt': node['attempt_id'],
           'native_ancestors_cloned': len(ancestors),
           'native_source_sha256': source_hash, 'native_score': node['score'],
           'score': result['score'], 'exact': result['exact'],
           'status': (result.get('verification') or {}).get('status'),
           'frontend_passed': (result.get('residual') or {}).get('frontend', {}).get('passed'),
           'best_source_sha256': result['source_sha256'], 'best_attempt': result['attempt_id'],
           'proposal_compiles': result['proposal_compiles'],
           'attempt_rows': attempts, 'lineage_edges': edges,
           'exact_rows': exact_rows,
           'edits_on_path': [entry for entry in result['log'] if entry.get('complete')],
           'private_db': str(db)}
REPORT.write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2), flush=True)
