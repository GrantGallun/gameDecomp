"""Read a coherent native checkpoint's metadata without hydrating every node."""
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import zlib

path = Path('/home/grant/decomp/runs/resume-pipeline-20260908/campaign.json')
pointer = json.loads(path.read_bytes())
with sqlite3.connect((path.parent / pointer['store']).as_uri() + '?mode=ro', uri=True) as conn:
    raw = conn.execute('SELECT manifest FROM commits WHERE id=?', (pointer['commit'],)).fetchone()[0]
    assert hashlib.sha256(raw).hexdigest() == pointer['sha256']
    manifest = json.loads(raw)
    raw = zlib.decompress(conn.execute('SELECT payload FROM objects WHERE hash=?', (manifest['metadata'],)).fetchone()[0])
    assert hashlib.sha256(raw).hexdigest() == manifest['metadata']
    metadata = json.loads(raw)
print(json.dumps({'commit': pointer['commit'], 'summary': metadata.get('summary'),
    'completed_items': metadata.get('fast_metrics', {}).get('completed_items'),
    'inflight': [{'function': j['function'], 'profile': j['profile']['name'],
                 'raw_receipt': j['raw'], 'raw_exists': Path(j['raw']).exists(),
                 'dispatch_age_seconds': time.time() - int(j['id'].split('-')[0]) / 1e9}
                for j in metadata.get('fast_inflight', [])],
    'native_pause': (path.parent / 'service.pause').exists()}, indent=2))
