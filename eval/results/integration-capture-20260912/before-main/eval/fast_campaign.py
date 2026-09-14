"""Resume evidence-v1 using incremental checkpoints and two isolated workers.

Supports ordered waves or resource-aware rolling dispatch. Each function's
original profile, source, budget and evidence key are retained. No concurrent workers touch a live candidate workspace or KB.
"""
import argparse
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
import copy
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import time

from eval import campaign_state, campaign_workers, completion_campaign as campaign, repair_yield


def worker(job):
    import fcntl
    # A surviving worker from an interrupted parent owns this lease until its
    # durable raw result exists. A resumed worker must not duplicate it.
    with Path(job['raw']+'.lock').open('a+b') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX)
        if Path(job['raw']).exists():
            return job['raw']
        return _worker(job)


def _worker(job):
    worker_started_at = time.time()
    setup_started = time.monotonic()
    from eval import fast_runtime
    from solver import compiler_recipe
    # This cache includes make/environment/file-existence observations beyond
    # its literal argument key. Match fresh-process semantics between jobs.
    compiler_recipe._resolve.cache_clear()
    root = Path(job['slot'])
    metrics = fast_runtime.install(root/'cache', job['pin_sha256'], job['model_lock'],
                                   model_parallel=job.get('model_parallel', 1))
    original_connect = sqlite3.connect
    try:
        return _execute_worker(job,metrics,original_connect,worker_started_at,setup_started)
    finally:
        sqlite3.connect = original_connect
        metrics.close()
        compiler_recipe._resolve.cache_clear()


def _execute_worker(job, metrics, original_connect, worker_started_at, setup_started):
    def connect(database, *args, **kwargs):
        conn = original_connect(database, *args, **kwargs)
        if str(database) == job['db']:
            def authorizer(action, table, column, database, origin):
                if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}:
                    allowed = (action == sqlite3.SQLITE_INSERT and table in campaign_workers.TABLES or
                               action == sqlite3.SQLITE_UPDATE and table == 'model_proposals' and column == 'child_attempt_id' or
                               table == 'sqlite_master')
                    if not allowed:
                        return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            conn.set_authorizer(authorizer)
        return conn
    sqlite3.connect = connect
    start = time.monotonic()
    # Scoped runtime wrappers and DB authorization are restored by _worker,
    # including after unexpected failures or interrupted raw receipt writes.
    import subprocess
    try:
        result = campaign.execute(repo=Path(job['repo']), db=Path(job['db']), function=job['function'],
                                  node=job['node'], profile=job['profile'], config=job['config'], out=Path(job['raw']))
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        result = {'status':'parked', 'source':job['node']['source'],
                  'source_sha256':job['node']['source_sha256'],
                  'blocker':{'status':'operational_or_intake_failure','error':f'{type(exc).__name__}: {exc}'}}
    result['wall_seconds'] = time.monotonic()-start
    result['worker_started_at'] = worker_started_at
    result['worker_finished_at'] = time.time()
    metrics['worker_setup_seconds'] = start-setup_started
    result['performance'] = metrics
    campaign_state.atomic(job['raw'], result)
    return job['raw']


def summary(state, selected):
    nodes = list(state['nodes'].values())
    state['summary'] = {'cohort_functions':len(nodes),
        'object_exact_or_integrated':sum(n['status'] in {'object_exact','integrated'} for n in nodes),
        'integrated':sum(n['status']=='integrated' for n in nodes),
        'function_exact_pending_integration':sum(n['status']=='function_exact_pending_integration' for n in nodes),
        'parked':sum(n['status']=='parked' for n in nodes),
        'stalled':sum(n['status']=='pending' and name not in state['repair_queue']['work_items']
                      for name,n in state['nodes'].items()),
        'work_remaining':selected is not None, 'complete_c_decompilation':False}
    state['status'] = ('paused_budget' if selected else
        'cohort_integrated' if nodes and all(n['status']=='integrated' for n in nodes) else
        'cohort_objects_exact' if nodes and all(n['status'] in {'object_exact','integrated'} for n in nodes) else
        'awaiting_integration' if any(n['status']=='function_exact_pending_integration' for n in nodes) else
        'stalled_requires_new_strategy_or_evidence')


def project(state):
    started = time.monotonic()
    queue, selected = campaign.repair_queue.project(state, campaign.PROFILES)
    state['repair_queue'] = queue
    state.setdefault('fast_metrics', {}).setdefault('queue_seconds', 0.)
    state['fast_metrics']['queue_seconds'] += time.monotonic()-started
    return selected


def validate_job(node, job, result):
    if (node.get('source_sha256') != job['node'].get('source_sha256') or
        campaign.repair_queue.evidence_key(node) != job['profile']['evidence_key']):
        raise ValueError('worker result is stale; refusing import')
    if hashlib.sha256(Path(node['source']).read_bytes()).hexdigest() != node['source_sha256']:
        raise ValueError('dispatch source changed outside controller')
    if hashlib.sha256(Path(result['source']).read_bytes()).hexdigest() != result['source_sha256']:
        raise ValueError('worker candidate hash mismatch')


def dispatch_items(state, jobs, capacity, model_workers, model_calls):
    """Maintain a model lane; preserve evidence-v1 priority within each resource.

    Only already-eligible profiles participate. CPU work retains capacity when
    available; unused capacity can serve either lane. Never dispatch a function
    twice, even when it still appears in the projected queue.
    """
    busy = {j['function'] for j in jobs}
    active_model = sum(bool(j['profile'].get('model')) for j in jobs)
    items = sorted((r for r in state['repair_queue']['work_items'].values()
                    if r['function'] not in busy), key=lambda r:r['priority'])
    profiles = {r['function']:campaign.repair_queue.next_profile(
        state['nodes'][r['function']], model_calls, campaign.PROFILES) for r in items}
    picked = []
    for _ in range(capacity):
        prefer_model = active_model < model_workers
        candidates = [r for r in items if bool(profiles[r['function']].get('model')) == prefer_model]
        if not items:
            break
        item = (candidates or items)[0]
        items.remove(item)
        active_model += bool(profiles[item['function']].get('model'))
        picked.append(item)
    return picked


def timed(metrics, name, operation, *args, **kwargs):
    started = time.monotonic()
    try:
        return operation(*args, **kwargs)
    finally:
        metrics[name] = metrics.get(name, 0.) + time.monotonic()-started


def effort_profile(profile, policy='profile'):
    """An explicit DEV amendment for the reasoned-alternative lane only."""
    if policy not in {'profile', 'medium'}:
        raise ValueError('reasoned effort must be profile or medium')
    result = copy.deepcopy(profile)
    result['requested_think'] = profile.get('think', 'low')
    result['effort_policy'] = policy
    if policy == 'medium' and profile['name'] == 'reasoned_alternative' and profile.get('think') == 'high':
        result['think'] = 'medium'
    return result


def bind_runtime_options(state, runtime_options):
    # An absent legacy setting always means the original profile behavior.
    # It must not inherit an opt-in medium policy from the new CLI invocation.
    previous_options = dict(state.get('runtime_options', {**runtime_options, 'reasoned_effort': 'profile'}))
    previous_options.setdefault('tasks_per_worker', 1)
    previous_options.setdefault('reasoned_effort', 'profile')
    if previous_options != runtime_options:
        raise ValueError('runtime options changed; explicit amendment required')
    state['runtime_options'] = dict(runtime_options)


def run(args):
    pipeline = getattr(args, 'dispatch', 'wave') == 'pipeline'
    model_parallel = getattr(args, 'model_parallel', 1)
    model_workers = getattr(args, 'model_workers', None) or model_parallel
    tasks_per_worker = getattr(args, 'tasks_per_worker', 1)
    reasoned_effort = getattr(args, 'reasoned_effort', 'profile')
    if reasoned_effort not in {'profile', 'medium'}:
        raise ValueError('reasoned effort must be profile or medium')
    if not 1 <= tasks_per_worker <= 64:
        raise ValueError('tasks per worker must be bounded between one and 64')
    if not args.resume or args.scheduler != 'evidence-v1' or not 1 <= args.workers <= 3:
        raise ValueError('fast runtime requires evidence-v1 resume and one to three workers')
    if not 1 <= model_parallel <= min(2, args.workers):
        raise ValueError('model parallelism must be one or two and fit the worker count')
    if not model_parallel <= model_workers <= args.workers:
        raise ValueError('model worker count must cover inference slots and fit the worker count')
    state_path = args.state
    run_dir = state_path.parent
    with campaign.campaign_lock(state_path.with_suffix('.lock')):
        session_start = time.monotonic()
        state = campaign_state.read(state_path)
        expected = {'repo':str(args.repo), 'db':str(args.db), 'project':str(args.project),
                    'model':args.model, 'endpoint':args.endpoint, 'model_calls':args.model_calls,
                    'timeout':args.timeout, 'num_predict':args.num_predict, 'scheduler':args.scheduler}
        if any(state['config'].get(k) != v for k,v in expected.items()):
            raise ValueError('resume configuration differs')
        runtime_options = {'dispatch':'pipeline' if pipeline else 'wave',
                            'workers':args.workers,'model_parallel':model_parallel,
                            'model_workers':model_workers,'tasks_per_worker':tasks_per_worker,
                            'reasoned_effort':reasoned_effort}
        bind_runtime_options(state, runtime_options)
        if state.get('inflight'):
            raise ValueError('finish legacy inflight item before changing runtime')
        pins = campaign._pins(args.project, args.repo)
        pins.update(campaign.frozen_wavefront.file_hashes([Path(p) for p in state['pins']
                    if Path(p).is_relative_to(args.repo/'nonmatchings')]))
        if pins != state['pins']:
            raise ValueError('frozen inputs changed; explicit amendment required')
        with sqlite3.connect(args.db) as conn:
            inventory = list(conn.execute('SELECT name,addr,size,insn_count FROM functions ORDER BY name'))
        if campaign.digest(inventory) != state['inventory_sha256']:
            raise ValueError('inventory changed')
        if campaign.frozen_wavefront.model_digest(args.endpoint,args.model) != state['model_digest']:
            raise ValueError('model changed')
        pin_hash = campaign.digest(pins)
        store = campaign_state.Store(state_path)
        slots = args.worker_root
        slots.mkdir(parents=True, exist_ok=True)
        artifacts = run_dir/(state_path.stem+'-artifacts')
        completed = 0
        metrics = state.setdefault('fast_metrics', {})
        for k in ('completed_items','improved_items','exact_items','worker_seconds','model_queue_seconds',
                  'model_seconds','checkpoint_seconds','controller_seconds','session_seconds'):
            metrics.setdefault(k, 0)
        session_base = metrics['session_seconds']
        metrics['startup_seconds'] = metrics.get('startup_seconds',0.) + time.monotonic()-session_start
        def save_session(*, changed=(), finished=False):
            # Persist measured elapsed time at every checkpoint. The UI may
            # extend this anchor only while this same controller is alive.
            metrics['session_seconds'] = session_base+time.monotonic()-session_start
            metrics['session_pid'] = os.getpid()
            metrics['session_live_since'] = None if finished else time.time()
            for label, count in [('improvements','improved_items'),('exact','exact_items')]:
                metrics[label+'_per_hour'] = 3600*metrics[count]/max(1,metrics['session_seconds'])
            elapsed = store.save(state,changed=changed)
            if not finished:
                metrics['checkpoint_seconds'] += elapsed
        selected = project(state)
        summary(state, selected)
        if not state.get('fast_inflight'):
            save_session()
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context('spawn'),
                                 max_tasks_per_child=tasks_per_worker) as pool:
            futures = {}
            while completed < args.max_work_items or state.get('fast_inflight'):
                jobs = state.get('fast_inflight', [])
                can_dispatch = (not (run_dir/'service.pause').exists() and selected is not None
                                and completed+len(jobs) < args.max_work_items
                                and len(jobs) < args.workers and (pipeline or not jobs))
                if can_dispatch:
                    controller_start = time.monotonic()
                    timed(metrics,'pin_verify_seconds',campaign.frozen_wavefront.verify_files,pins)
                    items = sorted(state['repair_queue']['work_items'].values(), key=lambda row:row['priority'])
                    # No two jobs for the same function may be inflight together.
                    capacity = min(args.workers-len(jobs), args.max_work_items-completed-len(jobs))
                    if pipeline:
                        items = dispatch_items(state, jobs, capacity, model_workers, args.model_calls)
                    else:
                        items = items[:capacity]
                    occupied = {Path(j['slot']).name for j in jobs}
                    free_slots = [i for i in range(args.workers) if str(i) not in occupied]
                    for slot_index, item in zip(free_slots, items):
                        function = item['function']
                        node = state['nodes'][function]
                        profile = campaign.repair_queue.next_profile(node,args.model_calls,campaign.PROFILES)
                        profile = effort_profile(profile, reasoned_effort)
                        if profile['name'] == 'intake':
                            raise ValueError('fast runtime currently requires completed intake')
                        slot = slots/str(slot_index)
                        slot.mkdir(parents=True,exist_ok=True)
                        marker = slot/'database-cutoffs.json'
                        prior = json.loads(marker.read_bytes()) if marker.exists() else None
                        cutoffs = timed(metrics,'database_sync_seconds',campaign_workers.synchronize,
                                        args.db,slot/'worker.sqlite',prior)
                        campaign_state.atomic(marker,cutoffs)
                        repo = timed(metrics,'workspace_isolate_seconds',campaign_workers.isolate,
                                     args.repo,slot/'repo',function)
                        tag = f'{time.time_ns()}-{function}'
                        jobs.append({'id':tag, 'slot':str(slot), 'db':str(slot/'worker.sqlite'),
                            'repo':str(repo), 'function':function,'node':copy.deepcopy(node),'profile':profile,
                            'config':state['config'], 'cutoffs':cutoffs, 'pin_sha256':pin_hash,
                            'model_parallel':model_parallel, 'model_lock':str(slots/'model.lock'), 'raw':str(artifacts/(tag+'.private.json')),
                            'receipt':str(artifacts/(tag+'.json'))})
                    state['fast_inflight'] = jobs
                    metrics['controller_seconds'] += time.monotonic()-controller_start
                    save_session()
                if not jobs:
                    break
                for job in jobs:
                    if job['id'] not in futures and not Path(job['raw']).exists():
                        futures[job['id']] = timed(metrics,'submit_seconds',pool.submit,worker,job)
                # Raw receipts are replayed without a second model call. In
                # pipeline mode, completed functions can commit out of order.
                ready = [j for j in jobs if j['id'] not in futures]
                if not pipeline:
                    job = jobs[0]
                elif ready:
                    job = ready[0]
                else:
                    done, _ = wait(list(futures.values()), return_when=FIRST_COMPLETED)
                    job = next(j for j in jobs if futures[j['id']] in done)
                future = futures.pop(job['id'], None)
                if future is not None:
                    future.result()
                import_started = time.monotonic()
                metrics['result_ready_wait_seconds'] = metrics.get('result_ready_wait_seconds',0.) + max(
                    0.,time.time()-Path(job['raw']).stat().st_mtime)
                timed(metrics,'pin_verify_seconds',campaign.frozen_wavefront.verify_files,pins)
                node = state['nodes'][job['function']]
                raw = json.loads(Path(job['raw']).read_bytes())
                validate_job(node,job,raw)
                amap, pmap = timed(metrics,'database_merge_seconds',campaign_workers.merge,
                                  args.db,job['db'],job['cutoffs'],job['id'])
                result = campaign_workers.remap(raw,amap,pmap)
                result['private_lineage'] = {'raw_receipt':job['raw'], 'attempt_ids':amap,
                                            'proposal_ids':pmap, 'dispatch_profile':job['profile']}
                # Original reports preserve private IDs; this canonical receipt
                # and its explicit mapping own imported campaign lineage.
                campaign_state.atomic(job['receipt'],result)
                before_status = {'status': node['status']}
                timed(metrics,'accept_seconds',campaign.accept,node,job['profile'],result,Path(job['receipt']))
                repair_yield.record(metrics, before_status, node, job['profile'], result)
                metrics['import_seconds'] = metrics.get('import_seconds',0.)+time.monotonic()-import_started
                state['fast_inflight'] = [j for j in state['fast_inflight'] if j['id'] != job['id']]
                completed += 1
                metrics['completed_items'] += 1
                metrics['improved_items'] += int(bool(result.get('best_score_improved')))
                metrics['exact_items'] += int(bool(result.get('exact')))
                metrics['worker_seconds'] += result['wall_seconds']
                for k in ('model_queue_seconds','model_seconds'):
                    metrics[k] += result['performance'][k]
                for k,v in result['performance'].items():
                    if k.startswith(('layout_','compile_','semantic_','panel_init_','target_work_')) or k in {
                        'model_calls','model_load_seconds','model_prompt_seconds',
                        'model_generation_seconds','model_prompt_tokens','model_generated_tokens',
                        'worker_setup_seconds'}:
                        metrics[k] = metrics.get(k,0)+v
                selected = project(state)
                summary(state,selected)
                save_session(changed=(job['function'],))
                print(json.dumps({'function':job['function'],'profile':job['profile']['name'],
                    'status':node['status'],'score':node.get('score'),'performance':result['performance']}),flush=True)
        save_session(finished=True)
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repo','db','project','state','worker-root'):
        parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--scheduler',default='evidence-v1')
    parser.add_argument('--workers',type=int,default=2)
    parser.add_argument('--dispatch',choices=('wave','pipeline'),default='wave')
    parser.add_argument('--model-parallel',type=int,default=1)
    parser.add_argument('--model-workers',type=int,help='model preparation jobs, including queued inference')
    parser.add_argument('--reasoned-effort',choices=('profile','medium'),default='profile',
                        help='DEV amendment: use medium only for high-effort reasoned_alternative jobs')
    parser.add_argument('--tasks-per-worker',type=int,default=1,
                        help='bounded jobs per process before recycling (1-64; amendment required)')
    parser.add_argument('--max-work-items',type=int,default=10)
    parser.add_argument('--model-calls',type=int,default=3)
    parser.add_argument('--model',default='gpt-oss:20b')
    parser.add_argument('--endpoint',required=True)
    parser.add_argument('--timeout',type=int,default=240)
    parser.add_argument('--num-predict',type=int,default=6000)
    run(parser.parse_args())


if __name__ == '__main__':
    main()
