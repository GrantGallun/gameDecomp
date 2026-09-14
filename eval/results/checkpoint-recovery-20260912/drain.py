"""Reconcile existing private receipts using original frozen controller only."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import time
from eval import campaign_state

root = Path('/mnt/c/Code/gameDecomp')
run = root/'eval/results/resume-pipeline-20260908'
out = Path(__file__).resolve().parent/('drain-'+str(time.time_ns()))
out.mkdir()
assert (run/'service.pause').exists()
assert json.loads((run/'service-control.json').read_bytes())['paused'] is True
launch = json.loads((run/'launch.json').read_bytes())
command = list(launch['command'])
index = command.index('--max-work-items')+1
command[index] = '0'
# The supervisor normally appends --resume; launch.json stores its base command.
if '--resume' not in command:
    command.append('--resume')
before_pointer = (run/'campaign.json').read_bytes()
before = campaign_state.read(run/'campaign.json')
assert before.get('fast_inflight') and all(Path(j['raw']).is_file() for j in before['fast_inflight'])
record = {'command': command, 'cwd': str(run/'code'), 'started_at': time.time(),
          'launch_sha256': hashlib.sha256((run/'launch.json').read_bytes()).hexdigest(),
          'before_checkpoint': json.loads(before_pointer)['commit'],
          'before_status_counts': dict(Counter(n['status'] for n in before['nodes'].values())),
          'existing_jobs': [j['id'] for j in before['fast_inflight']],
          'scope': 'original frozen controller; zero new work; no persisted option change; supervisor stays paused'}
(out/'command.json').write_text(json.dumps(record, indent=2)+'\n')
(out/'pointer-before.json').write_bytes(before_pointer)
print(json.dumps({'output': str(out), 'checkpoint': record['before_checkpoint'], 'jobs': record['existing_jobs']}), flush=True)
with (out/'drain.log').open('wb') as stream:
    process = subprocess.run(command, cwd=run/'code', stdout=stream, stderr=subprocess.STDOUT, timeout=300)
record['returncode'] = process.returncode
after = campaign_state.read(run/'campaign.json')
record.update(after_checkpoint=json.loads((run/'campaign.json').read_bytes())['commit'],
              after_status_counts=dict(Counter(n['status'] for n in after['nodes'].values())),
              fast_inflight=[j['id'] for j in after.get('fast_inflight', [])],
              status=after['status'], paused=(run/'service.pause').exists(),
              launch_unchanged=record['launch_sha256']==hashlib.sha256((run/'launch.json').read_bytes()).hexdigest(),
              runtime_options_unchanged=before['runtime_options']==after['runtime_options'],
              model_calls_delta=after['fast_metrics'].get('model_calls',0)-before['fast_metrics'].get('model_calls',0),
              finished_at=time.time())
# Imported metrics can include already completed inference. Confirm no new jobs
# instead of mislabeling those historical model totals as new generation.
record['new_job_ids'] = sorted({j.get('receipt') for n in after['nodes'].values() for j in n.get('jobs', [])}
                              - {j.get('receipt') for n in before['nodes'].values() for j in n.get('jobs', [])})
(out/'result.json').write_text(json.dumps(record, indent=2)+'\n')
print(json.dumps(record, indent=2), flush=True)
assert process.returncode == 0 and not after.get('fast_inflight') and record['paused']
assert record['launch_unchanged'] and record['runtime_options_unchanged']
