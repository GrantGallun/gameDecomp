"""Drain via kernel process-exit events, then use existing checked amendment stages.

No polling sleeps, no worker interruption, no source/node import. Every child
must pass before the next step; the existing installer rolls back on failure.
"""
from pathlib import Path
import importlib.util
import json
import os
import select
import sqlite3
import subprocess
import time
import zlib

HERE = Path(__file__).resolve().parent
CONTROL = HERE.parent / 'resume-pipeline-20260908'
REVISION = CONTROL / 'revisions/20261002-integration-headers'
NATIVE = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
PY = '/home/grant/decomp/sbk1/.venv/bin/python'
events = []


def record(event, **fields):
    events.append({'at': time.time(), 'event': event, **fields})
    (HERE / 'rollout-progress.json').write_text(json.dumps(events, indent=2) + '\n')
    print(json.dumps(events[-1]), flush=True)


def run(label, command, *, timeout=1800, cwd=None):
    with (HERE / (label + '.log')).open('w') as log:
        result = subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                timeout=timeout, check=False)
    record(label, returncode=result.returncode)
    if result.returncode:
        raise RuntimeError(label + ' failed; see ' + label + '.log')


def main():
    assert (CONTROL / 'service.pause').exists() and (NATIVE / 'service.pause').exists()
    service = json.loads((CONTROL / 'service.json').read_bytes())
    descriptors = []
    poller = select.poll()
    for role, expected in [('worker_pid', b'eval.fast_campaign'), ('pid', b'campaign_service')]:
        pid = service.get(role)
        path = Path(f'/proc/{pid}/cmdline')
        if pid and path.exists():
            assert expected in path.read_bytes(), 'unexpected process identity'
            try:
                descriptor = os.pidfd_open(pid)
            except ProcessLookupError:
                continue
            poller.register(descriptor, select.POLLIN)
            descriptors.append(descriptor)
    record('waiting_for_drained_pause', process_count=len(descriptors))
    deadline = time.monotonic() + 3600
    try:
        while descriptors:
            ready = poller.poll(max(1, int((deadline - time.monotonic()) * 1000)))
            if not ready or time.monotonic() >= deadline:
                raise RuntimeError('worker drain exceeded one hour; no install attempted')
            for descriptor, _ in ready:
                poller.unregister(descriptor)
                os.close(descriptor)
                descriptors.remove(descriptor)
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
    record('drained')
    run('stage', [PY, str(REVISION / 'stage.py')])
    run('staged-tests', [PY, str(REVISION / 'verify_stage.py')])
    run('apply', [PY, str(REVISION / 'apply_amendment.py'), '--apply'])
    before = json.loads((NATIVE / 'campaign.json').read_bytes())
    record('installed', checkpoint=before['commit'], summary=before['summary'])
    spec = importlib.util.spec_from_file_location('service_controls', CONTROL / 'code/eval/campaign_service.py')
    controls = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controls)
    controls.set_paused(CONTROL, False)
    command = controls.command_for(CONTROL, 10)
    command[command.index('--max-work-items') + 1] = '0'
    try:
        run('integration-canary', command, cwd=CONTROL / 'code')
    finally:
        run('resume', [PY, '-m', 'eval.campaign_service', '--run', str(CONTROL), '--batch', '10', 'resume'],
            cwd=CONTROL / 'code', timeout=120)
    after = json.loads((NATIVE / 'campaign.json').read_bytes())
    with sqlite3.connect((NATIVE / after['store']).as_uri() + '?mode=ro', uri=True) as conn:
        manifest = json.loads(conn.execute('SELECT manifest FROM commits WHERE id=?', (after['commit'],)).fetchone()[0])
        digest = manifest['nodes']['checkMainMenuSecretCode']
        node = json.loads(zlib.decompress(conn.execute('SELECT payload FROM objects WHERE hash=?', (digest,)).fetchone()[0]))
    result = {'passed': node['status'] == 'integrated' and after['summary']['object_exact_or_integrated']
                                      >= before['summary']['object_exact_or_integrated'],
              'candidate_status': node['status'], 'install_checkpoint': before['commit'],
              'final_checkpoint': after['commit'], 'before': before['summary'], 'after': after['summary'],
              'scope': 'Previously certified, header-assisted candidate integrated by ordinary controller; no clean discovery claim'}
    (HERE / 'rollout-result.json').write_text(json.dumps(result, indent=2) + '\n')
    record('finished', **result)
    if not result['passed']:
        raise RuntimeError('canary did not integrate the motivating candidate')


if __name__ == '__main__':
    try:
        main()
    except BaseException as exc:
        record('error', detail=f'{type(exc).__name__}: {exc}')
        raise
