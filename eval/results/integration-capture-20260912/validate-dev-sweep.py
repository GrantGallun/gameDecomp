"""Run new sweep on an in-memory checkpoint; never save live campaign state."""
import json
from pathlib import Path
import time
import hashlib
import sqlite3
import zlib
from eval import campaign_integration, campaign_state

root = Path('/mnt/c/Code/gameDecomp')
path = root / 'eval/results/resume-pipeline-20260908/campaign.json'
# Pin pointer first. The actual selected checkpoint is loaded using a private
# pointer into the immutable shared object store; no SQLite database is copied.
pointer = json.loads(path.read_bytes())
store = (path.parent / pointer['store']).resolve()
with sqlite3.connect(f'file:{store}?mode=ro', uri=True) as conn:
    for commit, raw_manifest in conn.execute('SELECT id,manifest FROM commits WHERE id=3886'):
        manifest = json.loads(raw_manifest)
        key = manifest['metadata']
        raw_metadata = zlib.decompress(conn.execute('SELECT payload FROM objects WHERE hash=?', (key,)).fetchone()[0])
        assert hashlib.sha256(raw_metadata).hexdigest() == key
        metadata = json.loads(raw_metadata)
        if not metadata.get('fast_inflight') and not metadata.get('inflight'):
            pointer.update(commit=commit, sha256=hashlib.sha256(raw_manifest).hexdigest())
            break
    else:
        raise ValueError('no drained immutable checkpoint in latest 200 commits')
temporary = root / 'eval/results/integration-capture-20260912' / ('pointer-' + str(time.time_ns()) + '.json')
pointer['store'] = str(store)
temporary.write_text(json.dumps(pointer))
state = campaign_state.read(temporary)
if state.get('fast_inflight') or state.get('inflight'):
    raise ValueError('selected checkpoint has active jobs; choose a drained snapshot')
checkpoint = pointer['commit']
artifacts = root / 'eval/results/integration-capture-20260912/dev-sweep'
artifacts.mkdir(exist_ok=True)
before = {n: r['status'] for n, r in state['nodes'].items()}
print(json.dumps({'checkpoint': checkpoint, 'pending': [n for n, r in state['nodes'].items()
                                                       if r['status'] == 'function_exact_pending_integration']}), flush=True)
started = time.monotonic()
changed = campaign_integration.sweep(state, repo=Path(state['config']['repo']), db=Path(state['config']['db']),
                                      artifacts=artifacts, checkpoint=checkpoint)
result = {'checkpoint': checkpoint, 'elapsed_seconds': time.monotonic() - started, 'changed_in_memory': changed,
          'live_campaign_saved': False, 'integration_sweep': state.get('integration_sweep'),
          'before_statuses': {n: before[n] for n in changed}}
out = artifacts / ('result-' + str(time.time_ns()) + '.json')
out.write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({'result': str(out), 'changed_in_memory': changed,
                  'status': state.get('integration_sweep', {}).get('latest', {}).get('status'),
                  'elapsed_seconds': result['elapsed_seconds']}), flush=True)
