"""Drain eligible deterministic work in reviewed 15-item controller batches.

No production/frozen code changes. Each batch uses the delivery canary's locks,
ratchet, model-call assertions, input verification and durable pause restoration.
Create STOP in this directory to stop between batches; native service.pause stops
the active controller at a work boundary. Never starts the model supervisor.
"""
from __future__ import annotations

import argparse
import collections
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
CONTROL = HERE.parent / 'resume-pipeline-20260908'
HELPER = CONTROL / 'revisions/20260926-delivery/bounded_canary.py'
BATCH_SIZE = 15


def load_helper():
    spec = importlib.util.spec_from_file_location('delivery_canary', HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def atomic(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def audit(output):
    helper = load_helper()
    sys.path.insert(0, str(helper.FROZEN))
    from eval import campaign_state, completion_campaign
    from solver import repair_queue
    state = campaign_state.read(helper.STATE)
    queue, selected = repair_queue.project(state, completion_campaign.PROFILES)
    nodes = helper.node_summary(state)
    work = queue['work_items']
    receipt = dict(helper.snapshot(), nodes=nodes,
        states=dict(collections.Counter(n['status'] for n in nodes.values())),
        eligible=len(work),
        profiles=dict(collections.Counter(item['profile'].split('@')[0] for item in work.values())),
        inflight=len(state.get('fast_inflight', [])),
        native_pause=helper.PAUSE.exists(), control_pause=(CONTROL / 'service.pause').exists(),
        research_exact=sorted(helper.research_exact()),
        campaign_attempt_exact=sorted(helper.campaign_attempt_exact()),
        next=selected, recorded_at=time.time())
    atomic(output, receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--batch', type=int)
    parser.add_argument('--audit', action='store_true')
    parser.add_argument('--max-batches', type=int, default=1000)
    args = parser.parse_args()
    if args.audit:
        result = audit(HERE / 'current.json')
        print(json.dumps({k: v for k, v in result.items() if k not in {
            'nodes', 'research_exact', 'campaign_attempt_exact'}}, indent=2))
        return
    if not args.run:
        raise SystemExit('pass --run to execute the authorized deterministic frontier')
    if args.batch is not None:
        helper = load_helper()
        helper.HERE = HERE / f'batch-{args.batch:04d}'
        helper.HERE.mkdir(exist_ok=False)
        sys.argv = [str(HELPER), '--run', '--max-work-items', str(BATCH_SIZE), '--timeout-seconds', '7200']
        helper.main()
        receipt = json.loads((helper.HERE / 'canary.json').read_bytes())
        newly_parked = sorted(name for name, row in receipt['changed_nodes'].items()
            if row['before']['status'] != 'parked' and row['after']['status'] == 'parked')
        if newly_parked:
            raise RuntimeError(f'newly parked nodes require diagnosis before continuing: {newly_parked}')
        return
    with (HERE / 'frontier.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (HERE / 'baseline.json').exists():
            raise RuntimeError('run already has a baseline; preserve it and diagnose before restarting')
        before = audit(HERE / 'baseline.json')
        if before['inflight'] or not before['native_pause'] or not before['control_pause']:
            raise RuntimeError('requires idle campaign with both durable pause markers')
        progress = {'pid': os.getpid(), 'started_at': time.time(), 'status': 'running',
                    'completed_batches': 0, 'completed_items': 0, 'new_attempts': 0,
                    'baseline_commit': before['commit'], 'baseline_exact':
                    before['summary']['object_exact_or_integrated'], 'batches': []}
        atomic(HERE / 'progress.json', progress)
        try:
            for index in range(1, args.max_batches + 1):
                if (HERE / 'STOP').exists():
                    progress['status'] = 'stop_requested'
                    break
                command = [sys.executable, str(Path(__file__).resolve()), '--run', '--batch', str(index)]
                result = subprocess.run(command, check=False)
                receipt_path = HERE / f'batch-{index:04d}/canary.json'
                if result.returncode:
                    raise RuntimeError(f'batch {index} exited {result.returncode}; inspect its receipt/log')
                receipt = json.loads(receipt_path.read_bytes())
                session = receipt['last_session']
                progress['completed_batches'] = index
                progress['completed_items'] += session['completed_items']
                progress['new_attempts'] += receipt['after']['attempts'] - receipt['before']['attempts']
                progress['checkpoint'] = receipt['after']['commit']
                progress['exact_or_integrated'] = receipt['after']['summary']['object_exact_or_integrated']
                progress['updated_at'] = time.time()
                progress['batches'].append({'index': index, 'receipt': str(receipt_path),
                    'gained_exact_names': receipt['gained_exact_names'],
                    'completed_items': session['completed_items']})
                atomic(HERE / 'progress.json', progress)
                if session['stopped_reason'] == 'paused':
                    progress['status'] = 'paused'
                    break
                if not session['deterministic_work_remaining']:
                    progress['status'] = 'deterministic_frontier_drained'
                    break
                if not session['completed_items']:
                    raise RuntimeError('no progress while deterministic work remains')
            else:
                progress['status'] = 'batch_ceiling'
            after = audit(HERE / 'final.json')
            exact_states = {'object_exact', 'integrated', 'function_exact_pending_integration'}
            prior = {n for n, row in before['nodes'].items() if row['status'] in exact_states}
            now = {n for n, row in after['nodes'].items() if row['status'] in exact_states}
            progress['lost_exact_names'] = sorted(prior - now)
            progress['gained_exact_names'] = sorted(now - prior)
            progress['new_vs_raw_ledgers'] = sorted(now - prior - set(before['research_exact'])
                                                    - set(before['campaign_attempt_exact']))
            if prior - now or after['model_calls'] != before['model_calls'] or after['model_proposals'] != before['model_proposals']:
                raise RuntimeError('final ratchet/model invariant failed')
        except BaseException as exc:
            progress['status'] = 'failed'
            progress['error'] = repr(exc)
            raise
        finally:
            progress['finished_at'] = time.time()
            atomic(HERE / 'progress.json', progress)
        print(json.dumps(progress, indent=2), flush=True)


if __name__ == '__main__':
    main()
