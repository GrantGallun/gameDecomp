from pathlib import Path
p=Path(__file__).resolve().parents[3]/'eval/fast_campaign.py'
s=p.read_text()
a=s.index('        while completed < args.max_work_items:')
b=s.index("        metrics['session_seconds']",a)
old=s[a:b]
prep=old[old.index('                controller_start'):old.index('            # A durable raw receipt')]
prep=prep.replace("                jobs = []\n",'')
prep=prep.replace("                for slot_index, item in enumerate(items[:min(args.workers,args.max_work_items-completed)]):", """                capacity = min(args.workers-len(jobs), args.max_work_items-completed-len(jobs))
                if pipeline:
                    items = dispatch_items(state, jobs, capacity, model_parallel, args.model_calls)
                else:
                    items = items[:capacity]
                occupied = {Path(j['slot']).name for j in jobs}
                free_slots = [i for i in range(args.workers) if str(i) not in occupied]
                for slot_index, item in zip(free_slots, items):""")
prep=prep.replace("'model_lock':str(slots/'model.lock'),", "'model_parallel':model_parallel, 'model_lock':str(slots/'model.lock'),")
imp=old[old.index('                    campaign.frozen_wavefront.verify_files(pins)',old.index('            # A durable raw receipt')):]
# The loop now imports one durable completion and immediately refills its slot.
imp='\n'.join(line[4:] if line.startswith('    ') else line for line in imp.split('\n'))
block="""        with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context('spawn'),
                                 max_tasks_per_child=1) as pool:
            futures = {}
            while completed < args.max_work_items or state.get('fast_inflight'):
                jobs = state.get('fast_inflight', [])
                can_dispatch = (not (run_dir/'service.pause').exists() and selected is not None
                                and completed+len(jobs) < args.max_work_items
                                and len(jobs) < args.workers and (pipeline or not jobs))
                if can_dispatch:
"""
block+='\n'.join('    '+line if line else '' for line in prep.split('\n'))
block+="""                if not jobs:
                    break
                for job in jobs:
                    if job['id'] not in futures and not Path(job['raw']).exists():
                        futures[job['id']] = pool.submit(worker, job)
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
"""
block+=imp
s=s[:a]+block+s[b:]
s=s.replace("    parser.add_argument('--workers',type=int,default=2)","    parser.add_argument('--workers',type=int,default=2)\n    parser.add_argument('--dispatch',choices=('wave','pipeline'),default='wave')\n    parser.add_argument('--model-parallel',type=int,default=1)")
s=s.replace('Scheduling is explicitly a two-function dispatch wave: each function\'s original\nprofile, source, budget and evidence key are retained, and imports happen in\ndispatch order.', 'Supports ordered waves or resource-aware rolling dispatch. Each function\'s\noriginal profile, source, budget and evidence key are retained.')
p.write_text(s)
