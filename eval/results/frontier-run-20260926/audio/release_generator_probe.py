"""Read-only generator fire check on the retained native release candidate."""
import gc
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path('/mnt/c/Code/gameDecomp')
sys.path.insert(0, str(ROOT))
from eval.campaign_state import read
from solver import local_web_merge, regalloc_mutations

state_path = Path('/home/grant/decomp/runs/resume-pipeline-20260908/campaign.json')
state = read(state_path)
node = state['nodes']['releaseSoundEffectHandleNode']
attempt_id = node['attempt_id']
address = node['address']
node_hash = node['source_sha256']
del state, node
gc.collect()
db = Path('/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite')
with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as conn:
    source, diff, sampling, stored_hash, func_addr = conn.execute(
        'SELECT source_code,diff_summary,sampling,source_sha256,func_addr FROM attempts WHERE id=?',
        (attempt_id,)).fetchone()
if func_addr != address or stored_hash != node_hash or hashlib.sha256(source.encode()).hexdigest() != node_hash:
    raise RuntimeError('retained source identity mismatch')
sampling = json.loads(sampling or '{}')
direct = [(label, hashlib.sha256(candidate.encode()).hexdigest())
          for label, kind, candidate in local_web_merge.variants(source, 'releaseSoundEffectHandleNode')]
stream = []
for position, (label, kind, candidate) in enumerate(regalloc_mutations.variants(
        source, 'releaseSoundEffectHandleNode', diff or '',
        evidence={'source_attribution': sampling.get('source_attribution'),
                  'frontend': sampling.get('frontend'),
                  'compiler_recipe': sampling.get('compiler_recipe')}), 1):
    if kind == 'local_web_merge':
        stream.append({'position': position, 'label': label,
                       'source_sha256': hashlib.sha256(candidate.encode()).hexdigest()})
    if position >= 1000:
        break
print(json.dumps({'attempt_id': attempt_id, 'source_sha256': node_hash,
                  'direct': direct, 'stream': stream}, indent=2))
