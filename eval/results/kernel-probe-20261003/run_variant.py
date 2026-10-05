"""Bounded launcher: preserves logs and failures, kills only its own process group."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
p = argparse.ArgumentParser()
p.add_argument('name')
p.add_argument('--graphs', action='store_true')
p.add_argument('--graphs-only', action='store_true')
p.add_argument('--attention', default=None)
p.add_argument('--linear', default='auto')
p.add_argument('--timeout', type=int, default=600)
p.add_argument('--cases', nargs='+', default=['short:1', 'long:1', 'long:4'])
p.add_argument('--max-batched-tokens', type=int, default=8192)
args = p.parse_args()
cmd = [sys.executable, '-u', str(ROOT / 'probe.py'), '--name', args.name,
       '--out', str(ROOT), '--linear', args.linear]
cmd.extend(['--cases', *args.cases])
cmd.extend(['--max-batched-tokens', str(args.max_batched_tokens)])
if args.graphs:
    cmd.append('--graphs')
if args.graphs_only:
    cmd.append('--graphs-only')
if args.attention:
    cmd.extend(['--attention', args.attention])
started = time.time()
with (ROOT / (args.name + '.log')).open('w') as log:
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    status = {'command': cmd, 'pid': proc.pid, 'started': started}
    (ROOT / (args.name + '.process.json')).write_text(json.dumps(status, indent=2))
    try:
        status['exit_code'] = proc.wait(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        status['exit_code'] = proc.wait()
        status['timeout'] = True
    status['elapsed_s'] = time.time() - started
    (ROOT / (args.name + '.process.json')).write_text(json.dumps(status, indent=2))
print(json.dumps(status), flush=True)
if status['exit_code']:
    print('\n'.join((ROOT / (args.name + '.log')).read_text().splitlines()[-35:]))
raise SystemExit(status['exit_code'])
