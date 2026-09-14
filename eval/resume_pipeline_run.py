"""Launch a frozen, game-wide development replay with durable local receipts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

from eval import frozen_wavefront
from solver import llm


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    endpoint = llm.host()
    model = 'gpt-oss:20b'
    identity = frozen_wavefront.model_digest(endpoint, model)
    baseline = root / 'eval/results/kb-sbk1-rom-ranges-v1.sqlite'
    baseline_hash = hashlib.sha256(baseline.read_bytes()).hexdigest()
    if baseline_hash != '9f6ce8437a3a5c9657dfa47174c989b2ea9a553bed3cc41b12b12d3a407d8d5a':
        raise ValueError('immutable ROM-range baseline changed')
    with sqlite3.connect(f'file:{baseline}?mode=ro', uri=True) as db:
        counts = {name: db.execute('select count(*) from ' + name).fetchone()[0]
                  for name in ('functions', 'evidence', 'attempts', 'inference')}
    preflight = dict(endpoint=endpoint, model=model, model_digest=identity,
                     baseline_sha256=baseline_hash, counts=counts)
    print(json.dumps(preflight), flush=True)
    if args.preflight:
        return
    run = root / 'eval/results/resume-pipeline-20260908'
    run.mkdir(exist_ok=False)
    snapshot = run / 'code'
    snapshot.mkdir()
    for name in ('solver', 'eval', 'kb', 'patterns', 'tools', 'miner', 'tests'):
        shutil.copytree(root / name, snapshot / name,
                        ignore=shutil.ignore_patterns('results', '__pycache__', '.cache'))
    shutil.copy2(root / 'pytest.ini', snapshot / 'pytest.ini')
    database = run / 'campaign.sqlite'
    shutil.copy2(baseline, database)
    state = run / 'campaign.json'
    command = [sys.executable, '-m', 'eval.completion_campaign',
               '--repo', '/home/grant/decomp/sbk1', '--db', str(database),
               '--project', str(snapshot), '--state', str(state),
               '--scheduler', 'evidence-v1', '--max-work-items', '1000',
               '--model-calls', '3', '--model', model, '--endpoint', endpoint,
               '--timeout', '240', '--num-predict', '6000']
    manifest = {**preflight, 'command': command, 'started_at': time.time(),
                'deadline_hours': None,
                'regime': 'game-wide header-assisted development replay; current pipeline, existing bootstrap drafts, no historical repair seeds',
                'selection': 'entire inventory minus existing frozen held-out sets',
                'integration_requested': False,
                'code_hashes': frozen_wavefront.file_hashes(frozen_wavefront.code_paths(snapshot)),
                'status': 'running', 'pid': os.getpid()}
    path = run / 'launch.json'
    path.write_text(json.dumps(manifest, indent=2))
    with (run / 'pipeline.log').open('w') as log:
        while True:
            process = subprocess.Popen(command, cwd=snapshot, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            manifest['worker_pid'] = process.pid
            path.write_text(json.dumps(manifest, indent=2))
            print(json.dumps({'run': str(run), 'worker_pid': process.pid, 'time_limit': None}), flush=True)
            returncode = process.wait()
            checkpoint = json.loads(state.read_text()) if state.exists() else {}
            manifest.update(returncode=returncode, campaign_status=checkpoint.get('status'),
                            summary=checkpoint.get('summary'))
            if returncode != 0 or checkpoint.get('status') != 'paused_budget':
                manifest['status'] = 'worker_exited'
                break
            # Per-invocation work budgets are checkpoints, not a reason to stop
            # an explicitly unbounded run. Preserve the frozen configuration.
            if '--resume' not in command:
                command = command + ['--resume']
            manifest['resumptions'] = manifest.get('resumptions', 0) + 1
    manifest['finished_at'] = time.time()
    path.write_text(json.dumps(manifest, indent=2))
    print(json.dumps({k:v for k,v in manifest.items() if k != 'code_hashes'}), flush=True)


if __name__ == '__main__':
    main()
