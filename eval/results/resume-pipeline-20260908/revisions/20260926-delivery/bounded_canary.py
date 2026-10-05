"""One normal-controller deterministic canary; never starts the repeating service.

Requires --run. Holds the supervisor lock, leaves the control-side durable pause
in place, temporarily clears only the native dispatch pause, and restores it in
finally. Uses every frozen launch argument unchanged except max-work-items=5
and the ephemeral --deterministic-only flag.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
CONTROL = HERE.parents[1]
FROZEN = CONTROL / 'code'
NATIVE = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
STATE = NATIVE / 'campaign.json'
PAUSE = NATIVE / 'service.pause'


def snapshot() -> dict:
    pointer = json.loads(STATE.read_bytes())
    with sqlite3.connect(f'file:{NATIVE / "campaign.sqlite"}?mode=ro', uri=True) as conn:
        attempts = conn.execute('SELECT count(*) FROM attempts').fetchone()[0]
        proposals = conn.execute('SELECT count(*) FROM model_proposals').fetchone()[0]
    return {'commit': pointer['commit'], 'pointer_sha256': hashlib.sha256(STATE.read_bytes()).hexdigest(),
            'summary': pointer['summary'], 'attempts': attempts, 'model_proposals': proposals,
            'completed_items': pointer.get('fast_metrics', {}).get('completed_items', 0),
            'model_calls': pointer.get('fast_metrics', {}).get('model_calls', 0)}


def node_summary(state: dict) -> dict:
    return {name: {'status': node['status'], 'source_sha256': node.get('source_sha256'),
                   'score': node.get('score'), 'attempt_id': node.get('attempt_id'),
                   'jobs': len(node.get('jobs', [])),
                   'last_profile': (node.get('jobs') or [{}])[-1].get('profile')}
            for name, node in state['nodes'].items()}


def research_exact() -> set[str]:
    database = Path.home() / 'decomp/kb-sbk1.sqlite'
    if not database.is_file():
        raise RuntimeError(f'research DB unavailable for deduplication: {database}')
    with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as conn:
        return {name for (name,) in conn.execute(
            'SELECT DISTINCT f.name FROM attempts a JOIN functions f ON f.addr=a.func_addr '
            'WHERE a.exact=1')}


def campaign_attempt_exact() -> set[str]:
    with sqlite3.connect(f'file:{NATIVE / "campaign.sqlite"}?mode=ro', uri=True) as conn:
        return {name for (name,) in conn.execute(
            'SELECT DISTINCT f.name FROM attempts a JOIN functions f ON f.addr=a.func_addr '
            'WHERE a.exact=1')}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--max-work-items', type=int, default=5)
    parser.add_argument('--timeout-seconds', type=int, default=7200)
    args = parser.parse_args()
    if not args.run:
        raise SystemExit('dry by default; review the script, then pass --run')
    if not 1 <= args.max_work_items <= 15:
        raise ValueError('canary must have 1–15 work items')
    if not (CONTROL / 'service.pause').exists() or not PAUSE.exists():
        raise RuntimeError('both pause markers required before canary')
    launch = json.loads((CONTROL / 'launch.json').read_bytes())
    command = list(launch['command'])
    if command[command.index('--state') + 1] != str(STATE):
        raise RuntimeError('launch points to another state')
    if command[command.index('--project') + 1] != str(FROZEN):
        raise RuntimeError('launch points to another frozen project')
    if '--deterministic-only' in command:
        raise RuntimeError('frozen launch already has canary flag')
    command[command.index('--max-work-items') + 1] = str(args.max_work_items)
    if '--resume' not in command:
        command.append('--resume')
    command.append('--deterministic-only')
    supervisor = (CONTROL / 'resume-supervisor.lock').open('a+b')
    try:
        fcntl.flock(supervisor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state_lock = STATE.with_suffix('.lock').open('a+b')
        try:
            fcntl.flock(state_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            state_lock.close()  # The controller itself takes this lock.
        sys.path.insert(0, str(FROZEN))
        from eval import campaign_state  # noqa: E402
        state = campaign_state.read(STATE)
        if state.get('fast_inflight') or state.get('inflight'):
            raise RuntimeError('campaign has in-flight work')
        before = snapshot()
        nodes_before = node_summary(state)
        prior_exact = {name for name, row in nodes_before.items() if row['status'] in {
            'object_exact', 'integrated', 'function_exact_pending_integration'}}
        known_research_exact = research_exact()
        known_campaign_attempt_exact = campaign_attempt_exact()
        log_path = HERE / 'canary-controller.log'
        started_at = time.time()
        PAUSE.unlink()
        timed_out = False
        try:
            with log_path.open('wb') as log:
                proc = subprocess.Popen(command, cwd=FROZEN, stdout=log, stderr=subprocess.STDOUT,
                                        start_new_session=True)
                try:
                    returncode = proc.wait(timeout=args.timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        returncode = proc.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        returncode = proc.wait()
        finally:
            PAUSE.touch()
        after = snapshot()
        state_after = campaign_state.read(STATE)
        nodes_after = node_summary(state_after)
        current_exact = {name for name, row in nodes_after.items() if row['status'] in {
            'object_exact', 'integrated', 'function_exact_pending_integration'}}
        lost_exact = sorted(prior_exact - current_exact)
        gained_exact = sorted(current_exact - prior_exact)
        changed_nodes = {name: {'before': nodes_before[name], 'after': row}
                         for name, row in nodes_after.items() if row != nodes_before[name]}
        session = state_after.get('fast_metrics', {}).get('last_session')
        receipt = {'kind':'bounded-deterministic-canary', 'started_at':started_at,
                   'finished_at':time.time(), 'command':command, 'returncode':returncode,
                   'timed_out':timed_out,
                   'before':before, 'after':after, 'last_session':session,
                   'changed_nodes':changed_nodes, 'lost_exact_names':lost_exact,
                   'gained_exact_names':gained_exact,
                   'gained_exact_already_in_research_db':sorted(set(gained_exact) & known_research_exact),
                   'gained_exact_new_vs_research_db':sorted(set(gained_exact) - known_research_exact),
                   'gained_exact_already_in_campaign_attempts':sorted(set(gained_exact) & known_campaign_attempt_exact),
                   'gained_exact_new_vs_raw_ledger_union':sorted(set(gained_exact) -
                       (known_research_exact | known_campaign_attempt_exact)),
                   'log':str(log_path), 'control_pause_preserved':(CONTROL / 'service.pause').exists(),
                   'native_pause_restored':PAUSE.exists()}
        (HERE / 'canary.json').write_text(json.dumps(receipt, indent=2) + '\n')
        if timed_out or returncode:
            raise RuntimeError(f'controller exited {returncode}; inspect {log_path}')
        if after['completed_items'] - before['completed_items'] > args.max_work_items:
            raise RuntimeError('controller exceeded work-item bound')
        if state_after.get('fast_inflight') or state_after.get('inflight'):
            raise RuntimeError('controller left work in flight')
        if lost_exact:
            raise RuntimeError(f'prior exact nodes lost: {lost_exact}')
        if after['model_calls'] != before['model_calls'] or after['model_proposals'] != before['model_proposals']:
            raise RuntimeError('deterministic canary made model calls/proposals')
        if after['summary']['object_exact_or_integrated'] < before['summary']['object_exact_or_integrated']:
            raise RuntimeError('global exact-match ratchet decreased')
        if not session or session.get('mode') != 'deterministic_only':
            raise RuntimeError('controller did not record deterministic-only session')
        if (session.get('completed_items') != after['completed_items'] - before['completed_items']
                or session['completed_items'] > args.max_work_items):
            raise RuntimeError('session completed count differs from checkpoint delta or bound')
        print(json.dumps({'commit':after['commit'], 'completed_items':session['completed_items'],
                          'new_attempts':after['attempts']-before['attempts'],
                          'exact_or_integrated':after['summary']['object_exact_or_integrated'],
                          'stopped_reason':session['stopped_reason']}, indent=2))
    finally:
        if not PAUSE.exists():
            PAUSE.touch()
        supervisor.close()


if __name__ == '__main__':
    main()
