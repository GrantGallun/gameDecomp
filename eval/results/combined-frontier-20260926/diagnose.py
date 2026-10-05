"""Read-only diagnosis of the active frozen campaign's deterministic frontier.

Run in WSL. In-flight jobs are read from checkpoint metadata without hydrating
all nodes; their presence does not prove a worker process is alive. Once idle,
the frozen scheduler's selected profiles are filtered to describe deterministic
work. This does not compile or schedule jobs.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
import zlib

HERE = Path(__file__).resolve().parent
MAIN = HERE.parents[2]
CONTROL = MAIN / 'eval/results/resume-pipeline-20260908'
FROZEN = CONTROL / 'code'
NATIVE = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
STATE = NATIVE / 'campaign.json'
DEFAULT_BASELINE = 1003
EXAMPLES = 8


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checkpoint() -> tuple[bytes, dict, dict]:
    raw = STATE.read_bytes()
    pointer = json.loads(raw)
    if pointer.get('kind') != 'campaign-checkpoint-index-v1':
        raise RuntimeError('unexpected checkpoint pointer kind')
    store = NATIVE / pointer['store']
    if store.resolve() != STATE.with_suffix('.state.sqlite').resolve():
        raise RuntimeError('unexpected checkpoint object store')
    with sqlite3.connect(store.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        row = conn.execute('SELECT manifest FROM commits WHERE id=?', (pointer['commit'],)).fetchone()
        if row is None or sha_bytes(row[0]) != pointer['sha256']:
            raise RuntimeError('checkpoint manifest missing or corrupt')
        manifest = json.loads(row[0])
        row = conn.execute('SELECT payload FROM objects WHERE hash=?',
                           (manifest['metadata'],)).fetchone()
        if row is None:
            raise RuntimeError('checkpoint metadata missing')
        metadata = zlib.decompress(row[0])
        if sha_bytes(metadata) != manifest['metadata']:
            raise RuntimeError('checkpoint metadata corrupt')
    if STATE.read_bytes() != raw:
        raise RuntimeError('checkpoint advanced during diagnosis; rerun')
    return raw, pointer, json.loads(metadata)


def _job_age(job: dict, now: float) -> dict:
    ident = str(job.get('id', ''))
    prefix = ident.split('-', 1)[0]
    dispatched = int(prefix) / 1e9 if prefix.isdigit() else None
    if dispatched is not None and not (1_600_000_000 < dispatched <= now + 60):
        dispatched = None
    raw_path = Path(job['raw']) if job.get('raw') else None
    raw = json.loads(raw_path.read_bytes()) if raw_path and raw_path.is_file() else None
    profile = job.get('profile', {})
    return {
        'id': ident, 'function': job.get('function'),
        'profile': profile.get('name') if isinstance(profile, dict) else profile,
        'model': profile.get('model') if isinstance(profile, dict) else None,
        'state': 'raw_ready_for_import' if raw is not None else 'receipt_pending',
        'elapsed_since_dispatch_seconds': round(max(0, now - dispatched), 1) if dispatched else None,
        'recorded_worker_wall_seconds': raw.get('wall_seconds') if raw else None,
    }


def _reason(node: dict) -> str:
    if node.get('status') == 'parked':
        blocker = node.get('blocker') or {}
        return str(blocker.get('status') or blocker.get('reason') or 'parked_unspecified')
    if not node.get('source_sha256'):
        return 'no_candidate_source'
    residual = node.get('residual') or {}
    if residual.get('compiled') is False:
        return 'compile_failure'
    frontend = residual.get('frontend') or {}
    if frontend.get('passed') is False:
        return 'frontend_failure:' + str(frontend.get('status') or 'unspecified')
    semantic = node.get('semantic_validation') or {}
    if semantic.get('status') in ('unavailable', 'inconclusive'):
        return 'semantic_' + semantic['status']
    if semantic.get('status') == 'observed_failure':
        return 'semantic_counterexample'
    faults = residual.get('faults') or {}
    active = [name for name, value in faults.items() if value]
    if active:
        return 'byte_residual:' + ','.join(sorted(active)[:3])
    return 'strategy_exhausted_or_evidence_missing'


def diagnose_idle(raw: bytes, metadata: dict) -> dict:
    sys.path.insert(0, str(FROZEN))
    from eval import campaign_state, completion_campaign
    from solver import repair_queue

    state = campaign_state.read(STATE)
    if STATE.read_bytes() != raw:
        raise RuntimeError('checkpoint advanced while reading nodes; rerun')
    if state.get('fast_inflight') or state.get('inflight'):
        raise RuntimeError('job started during idle diagnosis; rerun')
    if state['config'].get('scheduler') != 'evidence-v1':
        raise RuntimeError('diagnosis currently requires the evidence-v1 scheduler')
    queue, _ = repair_queue.project(state, completion_campaign.PROFILES)
    work_items = queue['work_items']
    revision = repair_queue.binary_input_revision(state)
    deterministic, model_possible = Counter(), Counter()
    parked, stalled = Counter(), Counter()
    eligible_examples, stalled_examples, parked_examples = [], [], []
    errors = []
    for name, node in state['nodes'].items():
        status = node.get('status')
        if status == 'parked':
            reason = _reason(node)
            parked[reason] += 1
            if len(parked_examples) < EXAMPLES:
                parked_examples.append({'function': name, 'reason': reason})
            continue
        if status != 'pending':
            continue
        try:
            profile = (repair_queue.next_profile(node, state['config']['model_calls'],
                                                 completion_campaign.PROFILES, revision)
                       if name in work_items else None)
            alternative = (repair_queue.next_profile(node, 1, completion_campaign.PROFILES, revision)
                           if profile is None else None)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append({'function': name, 'error': f'{type(exc).__name__}: {exc}'})
            continue
        if profile is not None and not profile.get('model'):
            deterministic[profile['name']] += 1
            if len(eligible_examples) < EXAMPLES:
                eligible_examples.append({'function': name, 'profile': profile['name'],
                                          'lane': profile.get('lane'), 'reason': _reason(node)})
        else:
            model_profile = (profile if profile is not None and profile.get('model') else
                             alternative if alternative is not None and alternative.get('model') else None)
            reason = ('model_profile_waiting:' + model_profile['name']
                      if model_profile is not None and model_profile.get('model') else _reason(node))
            stalled[reason] += 1
            if model_profile is not None and model_profile.get('model'):
                model_possible[model_profile['name']] += 1
            if len(stalled_examples) < EXAMPLES:
                stalled_examples.append({'function': name, 'reason': reason,
                                         'model_profile_if_enabled': model_profile['name'] if model_profile else None})
    if STATE.read_bytes() != raw:
        raise RuntimeError('checkpoint advanced during profile diagnosis; rerun')
    if errors:
        state_label = 'diagnosis_error'
    elif deterministic:
        state_label = 'eligible_deterministic_jobs_remain'
    else:
        state_label = 'deterministic_frontier_drained'
    return {
        'state': state_label,
        'pending': sum(node.get('status') == 'pending' for node in state['nodes'].values()),
        'parked': sum(node.get('status') == 'parked' for node in state['nodes'].values()),
        'next_deterministic_profile_histogram': dict(sorted(deterministic.items())),
        'model_blocked_pending': sum(model_possible.values()),
        'model_profile_if_enabled_histogram': dict(sorted(model_possible.items())),
        'pending_without_deterministic_profile': sum(stalled.values()),
        'pending_without_deterministic_profile_reason_histogram': dict(sorted(stalled.items())),
        'parked_reason_histogram': dict(sorted(parked.items())),
        'eligible_examples': eligible_examples,
        'pending_without_deterministic_profile_examples': stalled_examples,
        'parked_examples': parked_examples,
        'profile_errors': errors[:EXAMPLES],
        'interpretation': ('Model profiles remain available for some pending nodes.' if model_possible else
                           'No model profile was identified for stalled pending nodes.'),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=int, default=DEFAULT_BASELINE,
                        help='object-exact or integrated count to compare against')
    parser.add_argument('--output', type=Path, help='optional report JSON path')
    args = parser.parse_args()
    raw, pointer, metadata = checkpoint()
    now = time.time()
    summary = pointer['summary']
    jobs = [_job_age(job, now) for job in metadata.get('fast_inflight', [])]
    if metadata.get('inflight'):
        jobs.append({'legacy_inflight': metadata['inflight']})
    report = {
        'kind': 'read-only-deterministic-frontier-diagnosis',
        'checkpoint_commit': pointer['commit'],
        'pointer_sha256': sha_bytes(raw),
        'campaign_status': pointer.get('status'),
        'state': 'jobs_in_flight' if jobs else None,
        'pause_control': (CONTROL / 'service.pause').exists(),
        'pause_native': (NATIVE / 'service.pause').exists(),
        'baseline_object_exact_or_integrated': args.baseline,
        'object_exact_or_integrated': summary['object_exact_or_integrated'],
        'exact_delta': summary['object_exact_or_integrated'] - args.baseline,
        'function_exact_pending_integration': summary.get('function_exact_pending_integration'),
        'summary_stalled': summary.get('stalled'),
        'summary': summary,
        'active_jobs': jobs,
    }
    if not jobs:
        report.update(diagnose_idle(raw, metadata))
    if STATE.read_bytes() != raw:
        raise RuntimeError('checkpoint advanced before report output; rerun')
    rendered = json.dumps(report, indent=2) + '\n'
    if args.output:
        output = args.output.resolve()
        if output.is_relative_to(CONTROL.resolve()) or output.is_relative_to(NATIVE.resolve()):
            raise RuntimeError('output cannot be inside the campaign or frozen project')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end='')


if __name__ == '__main__':
    main()
