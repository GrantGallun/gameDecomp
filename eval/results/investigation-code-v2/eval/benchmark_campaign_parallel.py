"""Isolated, zero-model campaign replay for bounded CPU-concurrency experiments.

Run as a script (not -m) so workers can import the campaign's frozen code.
`prepare` is read-only toward the campaign and works on Windows. `run` needs
Linux, a paused campaign, and its free campaign lock. It never integrates results.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import random
import shutil
import sqlite3
import statistics
import sys
import time


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


def artifact(run, reference):
    return run / 'campaign-artifacts' / Path(reference.replace('\\', '/')).name


def quantile_cohorts(rows, count):
    """Disjoint interleaved duration quantiles, selected before any new timings."""
    if count < 4 or len(rows) < 2 * count:
        raise ValueError('need >=4 jobs per cohort and >=2*count eligible functions')
    ordered = sorted(rows, key=lambda r: (r['historical_wall_seconds'], r['function']))
    picked = [ordered[math.floor((i + .5) * len(ordered) / (2 * count))]
              for i in range(2 * count)]
    return {'fit': picked[::2], 'validation': picked[1::2]}


def prepare(run, output, count):
    output.mkdir(parents=True, exist_ok=False)
    state_bytes = (run / 'campaign.json').read_bytes()
    state = json.loads(state_bytes)
    rows, timeline = [], []
    for function, node in state['nodes'].items():
        for index, job in enumerate(node['jobs']):
            if job['profile'] != 'compile_recovery':
                continue
            path = artifact(run, job['receipt'])
            result = json.loads(path.read_text(encoding='utf-8'))
            wall = result.get('wall_seconds')
            if wall is None:
                continue
            timeline.append({'start': int(path.name.split('-')[0]) / 1e9,
                             'wall': wall, 'function': function})
            if index != 1 or node['jobs'][0]['profile'] != 'intake':
                continue  # reconstruct a first repair from its original intake
            initial = json.loads(artifact(run, node['jobs'][0]['receipt']).read_text(encoding='utf-8'))
            if initial.get('status') == 'parked' or result.get('calls_attempted', 0):
                continue
            if not initial.get('source') or not initial.get('source_sha256'):
                continue
            rows.append({'function': function, 'historical_wall_seconds': wall,
                         'instruction_count': node['instruction_count'],
                         'dag_level': node['dag_level'], 'intake': initial,
                         'intake_job': node['jobs'][0],
                         'profile': {'name': 'compile_recovery', 'model': False,
                                     'deterministic_budget': 0}})
            break
    timeline.sort(key=lambda row: row['start'])
    intervals = [{'interval': b['start'] - a['start'], 'reported_wall': a['wall'],
                  'outside_reported_wall': b['start'] - a['start'] - a['wall']}
                 for a, b in zip(timeline, timeline[1:])
                 if 0 < b['start'] - a['start'] < 120 and
                 b['start'] - a['start'] >= a['wall']]
    summary = {k: statistics.mean(x[k] for x in intervals)
               for k in ('interval', 'reported_wall', 'outside_reported_wall')} if intervals else {}
    manifest = {'kind': 'campaign-parallel-benchmark', 'schema': 1,
                'created_at': time.time(), 'run_name': run.name,
                'checkpoint_sha256': hashlib.sha256(state_bytes).hexdigest(),
                'config': state['config'], 'pins': state['pins'],
                'eligible_functions': len(rows), 'cohorts': quantile_cohorts(rows, count),
                'historical': {'pairs': len(intervals), 'mean_seconds': summary,
                    'scope': 'Completed first compile-recovery jobs; intervals >=120s excluded. '
                             'Reported wall includes pre-execution hashing. Residual is NOT a measured serial fraction.'},
                'scope': 'First deterministic compile recovery, same intake sources and budgets; '
                         'private DB per process; no model calls; no production scheduler change.'}
    (output / 'checkpoint.json').write_bytes(state_bytes)
    save(output / 'manifest.json', manifest)
    print(json.dumps({'output': str(output), 'eligible': len(rows), 'historical': manifest['historical']}))


def fit_amdahl(observations):
    """Constrained least squares T(p)=A+B/p; residuals expose unmodeled contention."""
    xs = [1 / row['workers'] for row in observations]
    ys = [row['wall_seconds'] for row in observations]
    if len(set(xs)) < 2:
        raise ValueError('need at least two different worker counts')
    xm, ym = statistics.mean(xs), statistics.mean(ys)
    b = sum((x-xm)*(y-ym) for x, y in zip(xs, ys)) / sum((x-xm)**2 for x in xs)
    a = ym - b*xm
    if a < 0:
        a, b = 0., sum(x*y for x,y in zip(xs,ys))/sum(x*x for x in xs)
    if b < 0:
        a, b = ym, 0.
    residuals = [y-(a+b*x) for x,y in zip(xs,ys)]
    return {'serial_seconds': a, 'parallel_seconds': b,
            'effective_serial_fraction': a/(a+b) if a+b else None,
            'rmse_seconds': math.sqrt(statistics.mean(r*r for r in residuals)),
            'residual_seconds': residuals,
            'interpretation': 'Effective fit, not a directly measured serial fraction; '
                              'load imbalance and contention can appear in A.'}


def fit_contention(observations, cpu_limit):
    """Fit nonnegative T(p)=A+B/p+C*(p-1) by enumerating active constraints."""
    import itertools
    import numpy as np
    if len({r['workers'] for r in observations}) < 3:
        return None
    matrix = np.array([[1., 1/r['workers'], r['workers']-1.] for r in observations])
    target = np.array([r['wall_seconds'] for r in observations])
    candidates = []
    for size in range(1, 4):
        for columns in itertools.combinations(range(3), size):
            values = np.linalg.lstsq(matrix[:,columns], target, rcond=None)[0]
            if min(values) < -1e-9:
                continue
            coefficients = np.zeros(3)
            coefficients[list(columns)] = np.maximum(values, 0)
            residual = target-matrix@coefficients
            candidates.append((float(residual@residual), coefficients))
    error, (a,b,c) = min(candidates, key=lambda entry: entry[0])
    predictions = {p: float(a+b/p+c*(p-1)) for p in range(1,cpu_limit+1)}
    return {'serial_seconds': float(a), 'parallel_seconds': float(b),
            'contention_seconds_per_extra_worker': float(c),
            'rmse_seconds': math.sqrt(error/len(observations)),
            'continuous_optimum': math.sqrt(b/c) if c>0 else None,
            'predicted_seconds': predictions,
            'predicted_integer_optimum': min(predictions, key=predictions.get),
            'assumption': 'Linear overhead in p-1; local fit within tested CPU range only.'}


def recommend(rounds, memory_bytes, cpu_limit):
    valid = [r for r in rounds if r['cohort'] == 'fit' and r.get('valid')
             and r['peak_tree_rss_bytes'] <= memory_bytes and r['workers'] <= cpu_limit]
    medians = {p: statistics.median(r['wall_seconds'] for r in valid if r['workers'] == p)
               for p in sorted({r['workers'] for r in valid})}
    if not medians:
        return None
    best_time = min(medians.values())
    # Small/noisy gains do not justify consuming another CPU.
    best = min(p for p, wall in medians.items() if wall <= 1.05 * best_time)
    return {'workers': best, 'median_seconds_by_workers': medians,
            'rule': 'smallest measured worker count within 5% of fastest fit median, under CPU/RSS budget'}


_worker_db = None
_worker_config = None


def initialize_worker(project, db_slots, config):
    global _worker_db, _worker_config
    sys.path.insert(0, project)
    _worker_db = Path(db_slots.get())
    _worker_config = config
    os.setsid()  # dedicated group permits bounded cleanup of compiler descendants
    from eval import completion_campaign  # noqa: F401 -- outside timed jobs


def warm_worker(_):
    time.sleep(.1)
    return os.getpid()


def replay_job(job):
    from eval import completion_campaign as campaign
    row, output = job
    node = {'status': 'pending', 'jobs': [], 'dag_level': row['dag_level'],
            'instruction_count': row['instruction_count']}
    campaign.accept(node, {'name': 'intake'}, row['intake'], Path(row['intake_job']['receipt']))
    started = time.monotonic()
    result = campaign.execute(repo=Path(_worker_config['repo']), db=_worker_db,
                              function=row['function'], node=node, profile=row['profile'],
                              config=_worker_config, out=Path(output))
    result['benchmark_wall_seconds'] = time.monotonic() - started
    save(Path(output), result)
    if result.get('calls_attempted', 0):
        raise RuntimeError('zero-model budget violated')
    return {'function': row['function'], 'pid': os.getpid(),
            'wall_seconds': result['benchmark_wall_seconds'],
            'fingerprint': {'source_sha256': result.get('source_sha256'),
                            'score': result.get('score'), 'exact': result.get('exact'),
                            'compiled': result.get('residual', {}).get('compiled')},
            'receipt': str(output)}


def tree_rss(root_pid):
    """Conservative process-tree RSS; shared pages may be counted repeatedly."""
    entries = {}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry/'stat').read_text().rsplit(')', 1)[1].split()
            entries[int(entry.name)] = (int(fields[1]), int(fields[21])*os.sysconf('SC_PAGE_SIZE'))
        except (OSError, ValueError, IndexError):
            continue
    selected = {root_pid}
    while True:
        expanded = selected | {pid for pid, (parent, _) in entries.items() if parent in selected}
        if expanded == selected:
            break
        selected = expanded
    return sum(entries.get(pid, (0, 0))[1] for pid in selected)


def run_round(manifest, output, baseline, cohort, workers, repeat, deadline, memory_bytes):
    directory = output / f'{cohort}-p{workers}-r{repeat}'
    directory.mkdir()
    context = multiprocessing.get_context('spawn')
    slots = context.Queue()
    for slot in range(workers):
        db = directory / f'worker-{slot}.sqlite'
        shutil.copy2(baseline, db)
        slots.put(str(db))
    record = {'cohort': cohort, 'workers': workers, 'repeat': repeat, 'valid': False}
    with ProcessPoolExecutor(workers, mp_context=context, initializer=initialize_worker,
                             initargs=(manifest['config']['project'], slots, manifest['config'])) as pool:
        startup = time.monotonic()
        list(pool.map(warm_worker, range(workers), timeout=max(.1,deadline-time.monotonic())))
        record['pool_startup_seconds'] = time.monotonic()-startup
        jobs = [(row, str(directory/f"{row['function']}.json")) for row in manifest['cohorts'][cohort]]
        started = time.monotonic()
        futures = [pool.submit(replay_job, job) for job in jobs]
        peak = 0
        while not all(f.done() for f in futures):
            peak = max(peak, tree_rss(os.getpid()))
            if time.monotonic() > deadline or peak > memory_bytes:
                import signal
                for process in pool._processes.values():
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                raise RuntimeError('benchmark time or memory budget exceeded')
            time.sleep(.1)
        results = [f.result() for f in futures]
        record.update(wall_seconds=time.monotonic()-started, peak_tree_rss_bytes=peak,
                      results=results, valid=True)
    save(directory/'timing.json', record)
    return record


def run_benchmark(run, output, cpu_limit, memory_gib, seconds, repeats):
    if sys.platform != 'linux':
        raise RuntimeError('actual compiler benchmark requires the campaign Linux environment')
    if not (run/'service.pause').exists():
        raise RuntimeError('pause campaign at a durable boundary before benchmarking')
    manifest = json.loads((output/'manifest.json').read_text())
    sys.path.insert(0, manifest['config']['project'])
    from eval import completion_campaign as campaign, frozen_wavefront
    from solver import repair_queue
    deadline = time.monotonic() + seconds
    available = len(os.sched_getaffinity(0))
    if not 1 <= cpu_limit <= available or repeats < 2 or memory_gib <= 0 or seconds <= 0:
        raise ValueError('invalid budget; repetitions >=2 and workers <= available CPUs required')
    budget = int(memory_gib*1024**3)
    report = {'status': 'running', 'rounds': [], 'memory_budget_bytes': budget,
              'cpu_budget': cpu_limit, 'available_cpus': available,
              'scope': manifest['scope'], 'time_budget_seconds': seconds}
    with campaign.campaign_lock(run/'campaign.lock'):
        try:
            frozen_wavefront.verify_files(manifest['pins'])
            # All rounds inherit identical history; no benchmark writes to live DB.
            baseline = output/'baseline.sqlite'
            with sqlite3.connect(f"file:{run/'campaign.sqlite'}?mode=ro", uri=True) as source:
                with sqlite3.connect(baseline) as destination:
                    source.backup(destination)
            state = json.loads((output/'checkpoint.json').read_text())
            timings = []
            for _ in range(3):
                sample = {}
                for label, action in (
                    ('verify_files', lambda: frozen_wavefront.verify_files(manifest['pins'])),
                    ('queue_projection', lambda: repair_queue.project(state, campaign.PROFILES)),
                    ('checkpoint_write', lambda: campaign.agentrepair._atomic_json(output/'checkpoint-probe.json', state))):
                    start = time.monotonic()
                    action()
                    sample[label] = time.monotonic()-start
                timings.append(sample)
            report['controller_component_seconds'] = timings
            del state
            orders = []
            for cohort in ('fit', 'validation'):
                for repeat in range(repeats):
                    order = list(range(1, cpu_limit+1))
                    random.Random(20260909+repeat).shuffle(order)
                    if repeat % 2:
                        order.reverse()
                    orders.extend((cohort,p,repeat) for p in order)
            report['reserved_jobs'] = sum(len(manifest['cohorts'][c]) for c,_,_ in orders)
            disk_required = baseline.stat().st_size * sum(p for _,p,_ in orders)
            if shutil.disk_usage(output).free < disk_required * 1.2:
                raise RuntimeError('insufficient disk headroom for isolated DBs and durable receipts')
            for cohort, p, repeat in orders:
                if time.monotonic() >= deadline:
                    raise RuntimeError('benchmark wall budget exhausted before next round')
                row = run_round(manifest, output, baseline, cohort, p, repeat, deadline, budget)
                reference = next((r for r in report['rounds'] if r['cohort']==cohort), None)
                if reference:
                    expected = {r['function']:r['fingerprint'] for r in reference['results']}
                    row['valid'] = all(r['fingerprint']==expected[r['function']] for r in row['results'])
                report['rounds'].append(row)
                frozen_wavefront.verify_files(manifest['pins'])
                save(output/'report.json', report)
                print(json.dumps({k:v for k,v in row.items() if k!='results'}), flush=True)
            fit_rows = [r for r in report['rounds'] if r['cohort']=='fit' and r['valid']]
            report['amdahl_fit'] = fit_amdahl(fit_rows)
            report['contention_fit'] = fit_contention(fit_rows, cpu_limit)
            report['recommendation'] = recommend(report['rounds'], budget, cpu_limit)
            validation = [dict(r,cohort='fit') for r in report['rounds'] if r['cohort']=='validation']
            report['validation_recommendation'] = recommend(validation, budget, cpu_limit)
            report['status'] = 'complete' if all(r['valid'] for r in report['rounds']) else 'result_mismatch'
            if report['status'] != 'complete':
                report['recommendation'] = None
            # Validation observations are kept out of both model fitting and selection.
            fit = report['contention_fit']
            baseline_validation = [r['wall_seconds'] for r in validation if r['workers']==1 and r['valid']]
            if fit and baseline_validation:
                scale = statistics.median(baseline_validation)/fit['predicted_seconds'][1]
                report['validation_prediction'] = {
                    'normalization': 'scale frozen fit by validation single-worker runtime',
                    'scale': scale,
                    'errors': [{'workers':r['workers'], 'repeat':r['repeat'],
                        'predicted_seconds':scale*fit['predicted_seconds'][r['workers']],
                        'actual_seconds':r['wall_seconds']}
                        for r in validation if r['valid']]}
            report['limits'] = ['Warm process pool; startup measured separately.',
                'Private DB per worker; does not establish shared-DB scalability.',
                'RSS includes duplicate shared pages; 100ms sampling may miss brief peaks.',
                'CPU worker optimum only; GPU/model concurrency is unmeasured.',
                'Controller components measured separately; full parallel controller not implemented.']
        except Exception as error:
            report.update(status='blocked_or_budget_exhausted', error=f'{type(error).__name__}: {error}')
            raise
        finally:
            save(output/'report.json', report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare','run'])
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--jobs-per-cohort', type=int, default=8)
    parser.add_argument('--cpu-budget', type=int, default=4)
    parser.add_argument('--memory-gib', type=float, default=5)
    parser.add_argument('--seconds', type=int, default=1200)
    parser.add_argument('--repeats', type=int, default=2)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.run, args.out, args.jobs_per_cohort)
    else:
        run_benchmark(args.run, args.out, args.cpu_budget, args.memory_gib, args.seconds, args.repeats)


if __name__ == '__main__':
    main()
