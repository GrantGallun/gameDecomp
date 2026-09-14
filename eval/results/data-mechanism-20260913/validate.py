"""Check release bindings and resumed durable work without copying a database."""
import hashlib
import json
from pathlib import Path
import time

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
RUN = ROOT / 'eval/results/resume-pipeline-20260908'
manifest = json.loads((OUT / 'staged-manifest.json').read_bytes())
deployment = json.loads((OUT / 'deployment.json').read_bytes())
pointer = json.loads((RUN / 'campaign.json').read_bytes())
service = json.loads((RUN / 'service.json').read_bytes())
before = json.loads((RUN / 'revisions/20260913-data-and-small-functions/campaign.json').read_bytes())
checks = {
    'release_files_match': all(hashlib.sha256((RUN / 'code' / rel).read_bytes()).hexdigest() == row['new_sha256']
                               for rel, row in manifest.items()),
    'checkpoint_advanced': pointer['commit'] > deployment['checkpoint'],
    'new_work_completed': pointer['fast_metrics']['completed_items'] > before['fast_metrics']['completed_items'],
    'exact_ratchet': pointer['summary']['object_exact_or_integrated'] >= before['summary']['object_exact_or_integrated'],
    'service_running': service['status'] == 'running' and bool(service.get('worker_pid')),
    'fresh_heartbeat': time.time() - service.get('heartbeat_at', 0) < 30,
    'batch_preserved': service['batch_work_items'] == 10,
    'not_paused': not (RUN / 'service.pause').exists(),
}
record = {'checkpoint': pointer['commit'], 'checked_at': time.time(), 'checks': checks,
          'new_completed_work_items': pointer['fast_metrics']['completed_items'] - before['fast_metrics']['completed_items'],
          'summary': pointer['summary'],
          'service': {k: service.get(k) for k in ('status', 'pid', 'worker_pid', 'heartbeat_at', 'batch_work_items')}}
(OUT / 'live-validation.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record))
assert all(checks.values()), checks

