"""Read-only, source-bound recent work audit; never copies or writes live databases."""
import collections
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from eval import campaign_state

ROOT = Path('/mnt/c/Code/gameDecomp')
RUN = ROOT / 'eval/results/resume-pipeline-20260908'
OUT = ROOT / 'eval/results/zero-gain-20260913'
pointer_bytes = (RUN/'campaign.json').read_bytes()
pointer = json.loads(pointer_bytes)
with closing(sqlite3.connect((RUN/pointer['store']).as_uri()+'?mode=ro', uri=True)) as conn:
    state = campaign_state._hydrate(conn, pointer)
(OUT/'pointer.json').write_bytes(pointer_bytes)
jobs = []
for name, node in state['nodes'].items():
    for i, job in enumerate(node.get('jobs', [])):
        path = Path(job.get('receipt', ''))
        if not path.is_file():
            continue
        jobs.append((path.stat().st_mtime, name, i, job))
jobs.sort(reverse=True)
rows = []
for stamp, name, index, job in jobs[:300]:
    path = Path(job['receipt'])
    raw = path.read_bytes()
    receipt = json.loads(raw)
    perf = receipt.get('performance') or {}
    rows.append({'function': name, 'index': index, 'time': stamp, **job,
                 'receipt_sha256': hashlib.sha256(raw).hexdigest(),
                 **{k: receipt.get(k) for k in ('score','best_score_improved','exact','calls_attempted',
                     'invalid_proposals','incomplete_responses','wall_seconds','attempt_id')},
                 'result_source_sha256': receipt.get('source_sha256'),
                 'source_changed': receipt.get('source_sha256') != job.get('source_sha256'),
                 'semantic_status': (receipt.get('semantic_validation') or {}).get('status'),
                 'performance': perf, 'diagnostics': receipt.get('log', [])[-5:],
                 'private_lineage': receipt.get('private_lineage')})
summary = {'checkpoint': pointer['commit'], 'pointer_sha256': hashlib.sha256(pointer_bytes).hexdigest(),
           'read_at':time.time(), 'config_db':state['config']['db'],
           'summary':state.get('summary'), 'selected_recent':len(rows), 'available_jobs':len(jobs),
           'oldest_time':min(x['time'] for x in rows), 'newest_time':max(x['time'] for x in rows),
           'profiles':{}, 'repeats':[], 'rows':rows}
for profile in sorted({r['profile'] for r in rows}):
    group = [r for r in rows if r['profile'] == profile]
    summary['profiles'][profile] = {'items':len(group),
        'improved':sum(bool(r['best_score_improved']) for r in group),
        'exact':sum(bool(r['exact']) for r in group),
        'changed_source':sum(bool(r['source_changed']) for r in group),
        'model_calls':sum(r['performance'].get('model_calls',0) for r in group),
        'zero_model_work':sum(not r['performance'].get('model_generated_tokens',0) for r in group),
        'wall_seconds':sum(r['wall_seconds'] or 0 for r in group),
        'generation_seconds':sum(r['performance'].get('model_generation_seconds',0) for r in group),
        'semantic_status':dict(collections.Counter(r['semantic_status'] for r in group))}
for key, count in collections.Counter((r['function'], r['profile'],r['source_sha256']) for r in rows).most_common():
    if count > 1:
        group = [r for r in rows if (r['function'],r['profile'],r['source_sha256']) == key]
        summary['repeats'].append({'function':key[0],'profile':key[1],'source_sha256':key[2],
                                  'count':count,'evidence_keys':list({r.get('evidence_key') for r in group})})
(OUT/'audit.json').write_text(json.dumps(summary, indent=2))
print(json.dumps({k:v for k,v in summary.items() if k != 'rows'}, indent=2))
