"""Resume the authorized frozen campaign; never change its code or configuration."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time

run = Path(__file__).resolve().parent
lock = (run / 'resume-supervisor.lock').open('a+b')
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
# Verify no legacy worker holds the campaign before launching a supervisor.
with (run / 'campaign.lock').open('a+b') as check:
    fcntl.flock(check, fcntl.LOCK_EX | fcntl.LOCK_NB)
manifest = json.loads((run / 'launch.json').read_text())
command = list(manifest['command'])
if '--resume' not in command:
    command.append('--resume')
receipt = {'kind': 'frozen-campaign-resume-supervisor', 'pid': os.getpid(),
           'started_at': time.time(), 'command': command, 'runs': []}

def save():
    (run / 'resume-supervisor.json').write_text(json.dumps(receipt, indent=2))

retries = 0
while True:
    with (run / 'pipeline.log').open('a') as log:
        start = log.tell()
        worker = subprocess.Popen(command, cwd=run / 'code', stdout=log,
                                  stderr=subprocess.STDOUT)
        receipt.update(status='running', worker_pid=worker.pid)
        save()
        print(json.dumps({'worker_pid': worker.pid, 'supervisor_pid': os.getpid(),
                          'status': 'running', 'time_limit': None}), flush=True)
        code = worker.wait()
    state = json.loads((run / 'campaign.json').read_text())
    event = {'returncode': code, 'campaign_status': state.get('status'),
             'finished_at': time.time(), 'summary': state.get('summary')}
    receipt['runs'].append(event)
    receipt.update(status='worker_exited', last_outcome=event)
    save()
    if code == 0 and state.get('status') == 'paused_budget':
        retries = 0
        continue
    with (run / 'pipeline.log').open() as log:
        log.seek(start)
        tail = log.read()[-6000:]
    transient = (code != 0 and 'PermissionError' in tail
                 and '/.campaign.json.' in tail and '.tmp' in tail
                 and "-> '" + str(run / 'campaign.json') + "'" in tail)
    if transient and retries < 3:
        retries += 1
        print(json.dumps({'status': 'retry_checkpoint_rename', 'retry': retries}), flush=True)
        time.sleep(5 * retries)
        continue
    print(json.dumps(event), flush=True)
    break
