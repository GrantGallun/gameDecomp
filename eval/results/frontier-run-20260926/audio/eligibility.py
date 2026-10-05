"""Read-only frozen-scheduler projection for the current native audio node."""
import gc
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path('/mnt/c/Code/gameDecomp')
sys.path.insert(0, str(ROOT))
from eval.campaign_state import read
from eval.completion_campaign import PROFILES
from solver import repair_queue

FROZEN = ROOT / 'eval/results/resume-pipeline-20260908/code'
owner = 'solver/repair_queue.py'
assert hashlib.sha256((ROOT / owner).read_bytes()).digest() == hashlib.sha256((FROZEN / owner).read_bytes()).digest()
pointer = Path('/home/grant/decomp/runs/resume-pipeline-20260908/campaign.json')
state = read(pointer)
checkpoint = json.loads(pointer.read_text()).get('commit')
revision = repair_queue.binary_input_revision(state)
node = state['nodes']['audioThreadMain']
model_calls = state['config']['model_calls']
del state
gc.collect()
profile = repair_queue.next_profile(node, model_calls, PROFILES, revision)
key = repair_queue.evidence_key(node)
jobs = [{k: job.get(k) for k in ('profile', 'source_sha256', 'evidence_key', 'lane', 'status')}
        for job in node['jobs']]
target = node['source_sha256']
print(json.dumps({
    'checkpoint': checkpoint, 'status': node['status'], 'score': node['score'],
    'source_sha256': target, 'lane': repair_queue.lane(node).value,
    'evidence_key': key, 'binary_revision': revision,
    'stored_binary_revision': node.get('binary_type_revision'),
    'next_profile': profile, 'jobs': jobs,
    'same_source_profiles': [job['profile'] for job in jobs if job['source_sha256'] == target],
    'same_evidence_profiles': [job['profile'] for job in jobs if job['evidence_key'] == key],
    'job_counts': dict(Counter(job['profile'] for job in jobs)),
    'faults': (node.get('residual') or {}).get('faults'),
}, indent=2))
