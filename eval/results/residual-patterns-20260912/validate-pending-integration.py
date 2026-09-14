"""Validate audited checkpoint candidates only; never import into campaign state."""
import sys
sys.path = [p for p in sys.path if not p.endswith('residual-patterns-20260912')]
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import zlib

from eval.completion_campaign import preflight_integration, integrate_candidates

root = Path('/mnt/c/Code/gameDecomp')
folder = Path(__file__).resolve().parent
audit_path = folder / 'integration-audit.json'
audit_bytes = audit_path.read_bytes()
audit = json.loads(audit_bytes)
repo = Path(audit['configuration']['repo'])
db = Path(audit['configuration']['db'])
artifacts = folder / ('pending-integration-' + str(time.time_ns()))
artifacts.mkdir()
entries = [{key: node[key] for key in ('function', 'source', 'attempt_id', 'verification')}
           for node in audit['pending']]
with sqlite3.connect(f'file:{db.with_name("campaign.state.sqlite")}?mode=ro', uri=True) as conn:
    manifest = conn.execute('SELECT manifest FROM commits WHERE id=?', (audit['checkpoint'],)).fetchone()[0]
    node_keys = json.loads(manifest)['nodes']
    for item in audit['pending']:
        key = node_keys[item['function']]
        raw = zlib.decompress(conn.execute('SELECT payload FROM objects WHERE hash=?', (key,)).fetchone()[0])
        assert hashlib.sha256(raw).hexdigest() == key
        node = json.loads(raw)
        assert node['status'] == 'function_exact_pending_integration'
        assert all(node[field] == item[field] for field in ('source', 'source_sha256', 'attempt_id', 'verification'))
binding = {'kind': 'checkpoint-selected-isolated-integration-validation',
           'checkpoint': audit['checkpoint'], 'checkpoint_manifest_sha256': hashlib.sha256(manifest).hexdigest(),
           'audit_sha256': hashlib.sha256(audit_bytes).hexdigest(),
           'functions': [{key: node[key] for key in ('function', 'source_sha256', 'attempt_id', 'size')}
                         for node in audit['pending']],
           'canonical_files_before': {node['translation_unit']: hashlib.sha256((repo / node['translation_unit']).read_bytes()).hexdigest()
                                      for node in audit['pending']},
           'policy': 'Existing certificate/TU preflight and isolated full-ROM gate. No model calls, reference-source inference, canonical edits, or campaign imports.'}
(artifacts / 'binding.json').write_text(json.dumps(binding, indent=2) + '\n')
print(json.dumps({'artifacts': str(artifacts), 'checkpoint': audit['checkpoint'], 'functions': [e['function'] for e in entries]}), flush=True)
started = time.monotonic()
eligible, blocked = preflight_integration(repo=repo, db=db, entries=entries, artifacts=artifacts, tag='checkpoint-' + str(audit['checkpoint']))
print(json.dumps({'preflight_eligible': len(eligible), 'blocked': blocked}), flush=True)
survivors, records = integrate_candidates(repo=repo, db=db, entries=eligible, artifacts=artifacts) if eligible else ([], [])
result = {'checkpoint': audit['checkpoint'], 'elapsed_seconds': time.monotonic() - started,
          'eligible': [e['function'] for e in eligible], 'blocked': blocked,
          'whole_rom_verified_functions': [e['function'] for e in survivors], 'records': records,
          'canonical_files_unchanged': all(hashlib.sha256((repo / name).read_bytes()).hexdigest() == digest
                                           for name, digest in binding['canonical_files_before'].items()),
          'campaign_state_modified': False}
(artifacts / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2), flush=True)
