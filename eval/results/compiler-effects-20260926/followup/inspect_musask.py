"""Export only immutable pre-experiment routing metadata, no candidate C."""
import hashlib
import json
from pathlib import Path
import sqlite3
import zlib

HERE = Path(__file__).resolve().parent
original = json.loads((HERE.parent / 'overflow/recovery/stage-29289/campaign.pointer.before.json').read_text())
native = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
with sqlite3.connect((native / original['store']).as_uri() + '?mode=ro', uri=True) as db:
    raw = db.execute('SELECT manifest FROM commits WHERE id=?', (original['commit'],)).fetchone()[0]
    assert hashlib.sha256(raw).hexdigest() == original['sha256']
    refs = json.loads(raw)
    def load(key):
        raw = zlib.decompress(db.execute('SELECT payload FROM objects WHERE hash=?', (key,)).fetchone()[0])
        assert hashlib.sha256(raw).hexdigest() == key
        return json.loads(raw)
    node = load(refs['nodes']['MusAsk'])
    metadata = load(refs['metadata'])
result = {'checkpoint': original['commit'], 'scheduler': metadata['config'].get('scheduler'),
          'node': {k: node.get(k) for k in ('status', 'score', 'attempt_id', 'source_sha256', 'residual')},
          'semantic_validation': {k: (node.get('semantic_validation') or {}).get(k)
                                  for k in ('status', 'mismatch_count', 'passed', 'reason')},
          'jobs': [{k: job.get(k) for k in ('profile', 'source_sha256', 'receipt', 'calls_attempted',
                                           'proposal_compiles', 'status', 'evidence_key')}
                   for job in node.get('jobs', [])]}
(HERE / 'MusAsk-routing.json').write_text(json.dumps(result, indent=2))
print(json.dumps({'checkpoint': result['checkpoint'], 'status': node['status'],
                  'score': node['score'], 'jobs': len(result['jobs'])}))
