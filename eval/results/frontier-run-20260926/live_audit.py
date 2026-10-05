"""Read a coherent live checkpoint and compare changed nodes with the run baseline."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import zlib

EXACT = {'object_exact', 'integrated', 'function_exact_pending_integration'}
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--baseline', type=Path, required=True)
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
baseline = json.loads(args.baseline.read_bytes())
state = Path('/home/grant/decomp/runs/resume-pipeline-20260908/campaign.json')
pointer = json.loads(state.read_bytes())
with sqlite3.connect((state.parent / pointer['store']).as_uri() + '?mode=ro', uri=True) as conn:
    def manifest(commit, expected=None):
        raw = conn.execute('SELECT manifest FROM commits WHERE id=?', (commit,)).fetchone()[0]
        if expected is not None:
            assert hashlib.sha256(raw).hexdigest() == expected
        return json.loads(raw)

    def object_at(digest):
        raw = zlib.decompress(conn.execute('SELECT payload FROM objects WHERE hash=?',
                                           (digest,)).fetchone()[0])
        assert hashlib.sha256(raw).hexdigest() == digest
        return json.loads(raw)

    current = manifest(pointer['commit'], pointer['sha256'])
    old = manifest(baseline['commit'])
    assert current['nodes'].keys() == old['nodes'].keys()
    metadata = object_at(current['metadata'])
    changed = {name: object_at(digest) for name, digest in current['nodes'].items()
               if old['nodes'][name] != digest}

gains, losses, parked, improved = {}, [], [], []
with sqlite3.connect((state.parent / 'campaign.sqlite').as_uri() + '?mode=ro', uri=True) as conn:
    for name, node in changed.items():
        before = baseline['nodes'][name]
        if before['status'] in EXACT and node['status'] not in EXACT:
            losses.append(name)
        if before['status'] != 'parked' and node['status'] == 'parked':
            parked.append(name)
        if (node.get('score') or 0) > (before.get('score') or 0):
            improved.append(name)
        if before['status'] not in EXACT and node['status'] in EXACT:
            row = conn.execute('SELECT f.name,a.source_sha256,a.exact,a.strategy,a.parent_attempt_id FROM attempts a '
                               'JOIN functions f ON f.addr=a.func_addr WHERE a.id=?',
                               (node['attempt_id'],)).fetchone()
            assert row and row[:2] == (name, node['source_sha256'])
            if node['status'] == 'object_exact':
                assert row[2] == 1
            gains[name] = {key: node.get(key) for key in
                           ('status', 'score', 'source_sha256', 'attempt_id', 'verification')}
            gains[name].update(strategy=row[3], parent_attempt_id=row[4])
    attempts = conn.execute('SELECT count(*) FROM attempts').fetchone()[0]
    model_proposals = conn.execute('SELECT count(*) FROM model_proposals').fetchone()[0]

metrics = metadata.get('fast_metrics', {})
report = {'recorded_at': time.time(), 'baseline_commit': baseline['commit'],
          'commit': pointer['commit'], 'summary': metadata['summary'],
          'completed_items_delta': metrics.get('completed_items', 0) - baseline['completed_items'],
          'attempts_delta_at_read': attempts - baseline['attempts'],
          'model_calls_delta': metrics.get('model_calls', 0) - baseline['model_calls'],
          'model_proposals_delta_at_read': model_proposals - baseline['model_proposals'],
          'gains': gains, 'lost_exact': sorted(losses), 'newly_parked': sorted(parked),
          'score_improved': sorted(improved),
          'new_vs_baseline_raw_ledgers': sorted(set(gains) - set(baseline['research_exact'])
                                               - set(baseline['campaign_attempt_exact'])),
          'inflight': [{'function': job['function'], 'profile': job['profile']['name']}
                       for job in metadata.get('fast_inflight', [])],
          'native_pause': (state.parent / 'service.pause').exists(),
          'note': 'Node verdicts are coherent at commit; attempt totals may include later imports.'}
args.out.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({key: value for key, value in report.items() if key != 'gains'}, indent=2))
print('gained_exact_names:', ', '.join(sorted(gains)))
assert not losses and not parked and not report['model_calls_delta'] and not report['model_proposals_delta_at_read']
