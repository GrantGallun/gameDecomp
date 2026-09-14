"""Resume evidence-v1 using incremental checkpoints and two isolated workers.

Scheduling is explicitly a two-function dispatch wave: each function's original
profile, source, budget and evidence key are retained, and imports happen in
dispatch order. No concurrent workers touch a live candidate workspace or KB.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import copy
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import time

from eval import campaign_state, campaign_workers, completion_campaign as campaign


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
    from eval import fast_runtime
    root = Path(job['slot'])
    metrics = fast_runtime.install(root/'cache', job['pin_sha256'], job['model_lock'])
    original_connect = sqlite3.connect
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
    # Each worker is a fresh process: wrappers/caches never stack across jobs.
    import subprocess
    try:
        result = campaign.execute(repo=Path(job['repo']), db=Path(job['db']), function=job['function'],
                                  node=job['node'], profile=job['profile'], config=job['config'], out=Path(job['raw']))
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        result = {'status':'parked', 'source':job['node']['source'],
                  'source_sha256':job['node']['source_sha256'],
                  'blocker':{'status':'operational_or_intake_failure','error':f'{type(exc).__name__}: {exc}'}}
    result['wall_seconds'] = time.monotonic()-start
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


def run(args):
    if not args.resume or args.scheduler != 'evidence-v1' or not 1 <= args.workers <= 2:
        raise ValueError('fast runtime requires evidence-v1 resume and one or two workers')
    state_path = args.state
    run_dir = state_path.parent
    with campaign.campaign_lock(state_path.with_suffix('.lock')):
        state = campaign_state.read(state_path)
        expected = {'repo':str(args.repo), 'db':str(args.db), 'project':str(args.project),
                    'model':args.model, 'endpoint':args.endpoint, 'model_calls':args.model_calls,
                    'timeout':args.timeout, 'num_predict':args.num_predict, 'scheduler':args.scheduler}
        if any(state['config'].get(k) != v for k,v in expected.items()):
            raise ValueError('resume configuration differs')
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
        session_start = time.monotonic()
        selected = project(state)
        summary(state, selected)
        if not state.get('fast_inflight'):
            metrics['checkpoint_seconds'] += store.save(state)
        while completed < args.max_work_items:
            jobs = state.get('fast_inflight', [])
            if not jobs:
                if (run_dir/'service.pause').exists() or selected is None:
                    break
                controller_start = time.monotonic()
                campaign.frozen_wavefront.verify_files(pins)
                items = sorted(state['repair_queue']['work_items'].values(), key=lambda row:row['priority'])
                jobs = []
                # No two jobs for the same function may be inflight together.
                for slot_index, item in enumerate(items[:min(args.workers,args.max_work_items-completed)]):
                    function = item['function']
                    node = state['nodes'][function]
                    profile = campaign.repair_queue.next_profile(node,args.model_calls,campaign.PROFILES)
                    if profile['name'] == 'intake':
                        raise ValueError('fast runtime currently requires completed intake')
                    slot = slots/str(slot_index)
                    slot.mkdir(parents=True,exist_ok=True)
                    marker = slot/'database-cutoffs.json'
                    prior = json.loads(marker.read_bytes()) if marker.exists() else None
                    cutoffs = campaign_workers.synchronize(args.db,slot/'worker.sqlite',prior)
                    campaign_state.atomic(marker,cutoffs)
                    repo = campaign_workers.isolate(args.repo,slot/'repo',function)
                    tag = f'{time.time_ns()}-{function}'
                    jobs.append({'id':tag, 'slot':str(slot), 'db':str(slot/'worker.sqlite'),
                        'repo':str(repo), 'function':function,'node':copy.deepcopy(node),'profile':profile,
                        'config':state['config'], 'cutoffs':cutoffs, 'pin_sha256':pin_hash,
                        'model_lock':str(slots/'model.lock'), 'raw':str(artifacts/(tag+'.private.json')),
                        'receipt':str(artifacts/(tag+'.json'))})
                state['fast_inflight'] = jobs
                metrics['controller_seconds'] += time.monotonic()-controller_start
                metrics['checkpoint_seconds'] += store.save(state)
            # A durable raw receipt is replayed after interruption without another
            # model call. Each job process exits after one task to bound memory.
            with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context('spawn'),
                                     max_tasks_per_child=1) as pool:
                futures = {j['id']:pool.submit(worker,j) for j in jobs if not Path(j['raw']).exists()}
                for job in list(jobs):
                    if job['id'] in futures:
                        futures[job['id']].result()
                    campaign.frozen_wavefront.verify_files(pins)
                    node = state['nodes'][job['function']]
                    raw = json.loads(Path(job['raw']).read_bytes())
                    validate_job(node,job,raw)
                    amap, pmap = campaign_workers.merge(args.db,job['db'],job['cutoffs'],job['id'])
                    result = campaign_workers.remap(raw,amap,pmap)
                    result['private_lineage'] = {'raw_receipt':job['raw'], 'attempt_ids':amap,
                                                'proposal_ids':pmap, 'dispatch_profile':job['profile']}
                    # Original reports preserve private IDs; this canonical receipt
                    # and its explicit mapping own imported campaign lineage.
                    campaign_state.atomic(job['receipt'],result)
                    campaign.accept(node,job['profile'],result,Path(job['receipt']))
                    state['fast_inflight'] = [j for j in state['fast_inflight'] if j['id'] != job['id']]
                    completed += 1
                    metrics['completed_items'] += 1
                    metrics['improved_items'] += int(bool(result.get('best_score_improved')))
                    metrics['exact_items'] += int(bool(result.get('exact')))
                    metrics['worker_seconds'] += result['wall_seconds']
                    for k in ('model_queue_seconds','model_seconds'):
                        metrics[k] += result['performance'][k]
                    for k,v in result['performance'].items():
                        if k.startswith(('layout_','compile_','semantic_')):
                            metrics[k] = metrics.get(k,0)+v
                    selected = project(state)
                    summary(state,selected)
                    metrics['checkpoint_seconds'] += store.save(state,changed=(job['function'],))
                    print(json.dumps({'function':job['function'],'profile':job['profile']['name'],
                        'status':node['status'],'score':node.get('score'),'performance':result['performance']}),flush=True)
        metrics['session_seconds'] += time.monotonic()-session_start
        metrics['improvements_per_hour'] = 3600*metrics['improved_items']/max(1,metrics['session_seconds'])
        metrics['exact_per_hour'] = 3600*metrics['exact_items']/max(1,metrics['session_seconds'])
        store.save(state)
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repo','db','project','state','worker-root'):
        parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--scheduler',default='evidence-v1')
    parser.add_argument('--workers',type=int,default=2)
    parser.add_argument('--max-work-items',type=int,default=10)
    parser.add_argument('--model-calls',type=int,default=3)
    parser.add_argument('--model',default='gpt-oss:20b')
    parser.add_argument('--endpoint',required=True)
    parser.add_argument('--timeout',type=int,default=240)
    parser.add_argument('--num-predict',type=int,default=6000)
    run(parser.parse_args())


if __name__ == '__main__':
    main()
