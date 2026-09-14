"""Read-only campaign audit; writes only isolated timing output and temporary files."""
import collections
import hashlib
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

run = Path('/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908')
output = Path(__file__).resolve().parent
sys.path.insert(0, str(run / 'code'))
from eval import completion_campaign as campaign

raw = (run / 'campaign.json').read_bytes()
report = {'checked_at': time.time(), 'checkpoint_sha256': hashlib.sha256(raw).hexdigest(),
          'checkpoint_bytes': len(raw), 'scope': 'Read-only snapshot; live campaign continues, timings subject to contention.'}
state = json.loads(raw)
del raw
report['states'] = dict(collections.Counter(n['status'] for n in state['nodes'].values()))
report['inflight'] = state.get('inflight')
jobs = sorted((j['receipt'], f, j) for f, n in state['nodes'].items() for j in n['jobs'])[-61:]
rows = []
for ref, function, job in jobs:
    path = run / 'campaign-artifacts' / Path(ref.replace('\\', '/')).name
    result = json.loads(path.read_text())
    events = [a for e in result.get('transport_events', []) for a in e.get('attempts', [])]
    rows.append({'start': int(path.name.split('-')[0]) / 1e9, 'function': function,
                 'profile': job['profile'], 'wall': result.get('wall_seconds', 0),
                 'transport_seconds': sum(e.get('elapsed_seconds', 0) for e in events),
                 'transport_status': dict(collections.Counter(e.get('status') for e in events)),
                 'improved': bool(result.get('best_score_improved')), 'exact': bool(result.get('exact'))})
intervals = [{'seconds': b['start']-a['start'], 'outside_worker': b['start']-a['start']-a['wall']}
             for a,b in zip(rows, rows[1:]) if 0 < b['start']-a['start'] < 300]
report['recent'] = {'rows': rows, 'intervals_under_300s': intervals,
                    'worker_mean': statistics.mean(r['wall'] for r in rows),
                    'worker_median': statistics.median(r['wall'] for r in rows),
                    'outside_worker_mean': statistics.mean(r['outside_worker'] for r in intervals),
                    'outside_worker_median': statistics.median(r['outside_worker'] for r in intervals),
                    'transport_seconds': sum(r['transport_seconds'] for r in rows),
                    'worker_seconds': sum(r['wall'] for r in rows)}
timings = {}
for name, operation in [('verify_all_pins', lambda: campaign.frozen_wavefront.verify_files(state['pins'])),
                        ('queue_projection', lambda: campaign.repair_queue.project(state, campaign.PROFILES))]:
    start = time.monotonic()
    operation()
    timings[name] = time.monotonic() - start
for name, directory in [('native_wsl', '/tmp'), ('windows_mount', str(output))]:
    with tempfile.TemporaryDirectory(prefix='checkpoint-timing-', dir=directory) as temporary:
        path = Path(temporary) / 'checkpoint.json'
        start = time.monotonic()
        campaign.agentrepair._atomic_json(path, state)
        timings[name + '_checkpoint_write'] = time.monotonic() - start
        start = time.monotonic()
        restored = json.loads(path.read_bytes())
        timings[name + '_checkpoint_read'] = time.monotonic() - start
        assert restored == state
        del restored
report['component_seconds'] = timings
report['roundtrip_equal'] = True
report['pins_count'] = len(state['pins'])
report['queue_work_items'] = len(state['repair_queue']['work_items'])
report['live_campaign_modified'] = False
(output / 'report.json').write_text(json.dumps(report, indent=2))
print(json.dumps({k:v for k,v in report.items() if k != 'recent'}, indent=2))
print(json.dumps({k:v for k,v in report['recent'].items() if k not in {'rows', 'intervals_under_300s'}}, indent=2))
