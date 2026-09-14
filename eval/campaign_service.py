"""Durable, bounded worker supervisor for an existing completion campaign (WSL).

Keeps the campaign's frozen command except for its per-process work-item budget.
The campaign itself persists each work item; this layer replaces processes and
limits retries without deciding semantic or exactness outcomes.
"""
from __future__ import annotations

import argparse
from collections import Counter
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def read(path, *, summary_only=False):
    with Path(path).open(encoding='utf-8') as stream:
        value = json.load(stream)
    if value.get('kind') == 'campaign-checkpoint-index-v1':
        if summary_only:
            # Health polls and supervisor decisions use the commit pointer.
            # The worker fully verifies/hydrates the checkpoint before work.
            return {'status': value.get('status'), '_checkpoint_health': value.get('health') or
                    {'checkpoint_commit': value.get('commit'), 'checkpoint_health_unavailable': True}}
        # Service can be invoked as a script, outside a package import path.
        import importlib.util
        spec = importlib.util.spec_from_file_location('campaign_state_reader', Path(__file__).with_name('campaign_state.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.read(path)
    return value


def save(path, value):
    path = Path(path)
    temporary = path.with_name('.' + path.name + '.service.tmp')
    with temporary.open('w', encoding='utf-8', buffering=1024 * 1024) as stream:
        json.dump(value, stream, separators=(',', ':'))
        stream.write('\n')
    temporary.replace(path)


def locked(path):
    with Path(path).open('a+b') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


def command_for(run, batch):
    if batch < 1:
        raise ValueError('batch must be positive')
    command = list(read(run / 'launch.json')['command'])
    command[command.index('--max-work-items') + 1] = str(batch)
    if '--resume' not in command:
        command.append('--resume')
    return command


def checkpoint_health(state):
    if '_checkpoint_health' in state:
        return state['_checkpoint_health']
    nodes = list(state['nodes'].values())
    semantics = Counter(n['semantic_validation'].get('status') for n in nodes
                        if n.get('semantic_validation'))
    return {'functions': len(nodes), 'states': dict(Counter(n['status'] for n in nodes)),
            'semantic_functions': dict(semantics), 'inflight': state.get('inflight'),
            'parallel_inflight':[{k:j.get(k) for k in ('id','function','profile')} for j in state.get('fast_inflight',[])],
            'performance':state.get('fast_metrics')}


def retry_decision(returncode, status, failure_count, paused=False):
    if paused:
        return 'paused'
    if status == 'paused_inputs_changed':
        return 'needs_repair'
    if returncode == 0:
        return 'restart' if status == 'paused_budget' else 'finished'
    return 'retry' if failure_count < 3 else 'needs_repair'


def tail(path, size=8000):
    with path.open('rb') as stream:
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell() - size))
        return stream.read().decode('utf-8', errors='replace')


def health(run):
    result = {'checked_at': time.time(), 'run': str(run)}
    try:
        state = read(run / 'campaign.json', summary_only=True)
        result.update(checkpoint_health(state))
        result['checkpoint_age_seconds'] = round(time.time() - (run / 'campaign.json').stat().st_mtime)
        control = read(run / 'service-control.json') if (run / 'service-control.json').exists() else {}
        record = read(run / 'service.json') if (run / 'service.json').exists() else {}
        active = locked(run / 'resume-supervisor.lock') or locked(run / 'campaign.lock')
        if control.get('paused') or (run / 'service.pause').exists():
            result['status'] = 'pausing' if active else 'paused'
        elif active:
            result['status'] = 'running'
            if result['checkpoint_age_seconds'] > 7200:
                result.update(status='needs_review', error='No checkpoint advancement for two hours; inspect work and child activity before intervention.')
            # Detect process suspension as an intentional pause, never restart it.
            for pid in (record.get('pid'), record.get('worker_pid')):
                if pid and Path(f'/proc/{pid}/status').exists():
                    state_line = next(x for x in Path(f'/proc/{pid}/status').read_text().splitlines() if x.startswith('State:'))
                    if 'T' in state_line.split()[1]:
                        result['status'] = 'paused_process'
        elif record.get('status') == 'needs_repair' or state.get('status') == 'paused_inputs_changed':
            result['status'] = 'needs_repair'
        elif record.get('status') == 'finished':
            result['status'] = 'finished'
        else:
            result['status'] = 'stopped'
        if result['status'] == 'needs_repair':
            result['error'] = record.get('error', state.get('error'))
    except (OSError, ValueError, KeyError) as exc:
        result.update(status='needs_repair', error=f'{type(exc).__name__}: {exc}')
    return result


def supervise(run, batch):
    # Same lock as the old supervisor prevents accidental competing workers.
    with (run / 'resume-supervisor.lock').open('a+b') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if locked(run / 'campaign.lock'):
            return
        record = read(run / 'service.json') if (run / 'service.json').exists() else {}
        failures = record.get('consecutive_failures', 0)
        record.update(pid=os.getpid(), started_at=time.time(), batch_work_items=batch)
        command = command_for(run, batch)
        while True:
            control = read(run / 'service-control.json') if (run / 'service-control.json').exists() else {}
            if control.get('paused') or (run / 'service.pause').exists():
                record.update(status='paused', worker_pid=None)
                save(run / 'service.json', record)
                return
            state = read(run / 'campaign.json', summary_only=True)  # Corruption is never silently rolled back.
            # Validated rolling backup plus append-only result receipts already
            # produced by completion_campaign form the restart boundary.
            pointer = json.loads((run / 'campaign.json').read_bytes())
            # A small previous commit pointer retains the complete immutable
            # snapshot; do not re-expand the large legacy backup every batch.
            save(run / 'checkpoint.previous.json', pointer if pointer.get('kind') == 'campaign-checkpoint-index-v1' else state)
            del state
            with (run / 'pipeline.log').open('a') as log:
                worker = subprocess.Popen(command, cwd=run / 'code', stdout=log,
                                          stderr=subprocess.STDOUT, start_new_session=True)
                record.update(status='running', worker_pid=worker.pid, command=command,
                              heartbeat_at=time.time())
                save(run / 'service.json', record)
                while worker.poll() is None:
                    record['heartbeat_at'] = time.time()
                    save(run / 'service.json', record)
                    try:
                        # Keep the heartbeat interval, but resume immediately
                        # when a batch exits instead of waiting out the sleep.
                        worker.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        pass
                code = worker.returncode
            state = read(run / 'campaign.json', summary_only=True)
            failures = failures + 1 if code else 0
            decision = retry_decision(code, state.get('status'), failures,
                                      (run / 'service.pause').exists())
            record.update(status=decision, returncode=code, worker_pid=None,
                          consecutive_failures=failures, last_exit_at=time.time(),
                          completed_batches=record.get('completed_batches', 0) + int(code == 0))
            if code:
                record['error'] = tail(run / 'pipeline.log')
            else:
                record.pop('error', None)
            save(run / 'service.json', record)
            with (run / 'service-events.jsonl').open('a') as stream:
                stream.write(json.dumps(record) + '\n')
            del state
            if decision not in {'restart', 'retry'}:
                return
            if decision == 'retry':
                time.sleep(10 * failures)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--batch', type=int, default=10)
    parser.add_argument('action', choices=['check', 'ensure', 'supervise', 'pause', 'resume'])
    args = parser.parse_args()
    run = args.run.resolve()
    if args.action == 'supervise':
        supervise(run, args.batch)
        return
    if args.action == 'pause':
        save(run / 'service-control.json', {'paused': True, 'time': time.time()})
        (run / 'service.pause').touch()
    if args.action == 'resume':
        save(run / 'service-control.json', {'paused': False, 'time': time.time()})
        (run / 'service.pause').unlink(missing_ok=True)
        if (run / 'service.json').exists():
            record = read(run / 'service.json')
            if (record.get('status') in {'needs_repair', 'paused'}
                    and not locked(run / 'resume-supervisor.lock') and not locked(run / 'campaign.lock')):
                record.update(status='stopped', consecutive_failures=0)
                record.pop('error', None)
                save(run / 'service.json', record)
    result = health(run)
    if args.action in {'ensure', 'resume'} and result['status'] == 'stopped':
        with (run / 'service.log').open('a') as log:
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--run', str(run),
                              '--batch', str(args.batch), 'supervise'], stdin=subprocess.DEVNULL,
                             stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        result['status'] = 'starting'
    save(run / 'health.json', result)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
