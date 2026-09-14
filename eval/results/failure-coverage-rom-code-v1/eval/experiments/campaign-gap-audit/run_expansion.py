"""Preselect untouched medium/large DEV functions, then run a bounded campaign.

Run from the fixed code snapshot. No historical candidate or reference body is
used to choose the panel. Holdout exclusions remain enforced by the controller.
"""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from eval import agentrepair, completion_campaign, frozen_wavefront
from solver import llm


def historical_exposure(paths):
    names, addresses, receipts = set(),set(),[]
    for path in paths:
        with sqlite3.connect(f'file:{path.as_posix()}?mode=ro',uri=True) as conn:
            rows=sorted(conn.execute('SELECT DISTINCT f.addr,f.name FROM functions f JOIN attempts a ON a.func_addr=f.addr'))
            cutoff=conn.execute('SELECT COALESCE(MAX(id),0) FROM attempts').fetchone()[0]
        names.update(name for _,name in rows)
        addresses.update(address for address,_ in rows)
        receipts.append({'database':str(path),'attempt_cutoff':cutoff,'exposed_identities':len(rows),
            'identity_sha256':hashlib.sha256(json.dumps(rows).encode()).hexdigest()})
    return names,addresses,receipts


def paired_selection(rows, seed):
    """Two disjoint panels, stratified before either produces outcomes."""
    groups = {}
    for name, count, tu, leaf in rows:
        size = 'small' if count <= 64 else 'medium' if count <= 160 else 'large' if count <= 400 else 'huge'
        shape = 'leaf' if leaf == 1 else 'calling' if leaf == 0 else 'unknown_shape'
        stratum = ('sdk' if '/ultra/' in tu else 'game') + '_' + size + '_' + shape
        groups.setdefault(stratum, []).append({'function':name, 'instructions':count,
            'tu':tu, 'stratum':stratum, 'is_leaf':leaf,
            'selection_hash':hashlib.sha256((seed+name).encode()).hexdigest()})
    batches = [[], []]
    shortages = {}
    for key, group in sorted(groups.items()):
        ordered = sorted(group, key=lambda row:(row['selection_hash'], row['function']))
        if len(ordered) < 2:
            shortages[key] = len(ordered)
        for index, row in enumerate(ordered[:2]):
            batches[index].append(row)
    return batches, {key:len(group) for key,group in sorted(groups.items())}, shortages


def run_paired(args, project, model_identity):
    cohort_path = args.output.with_name(args.output.stem+'.cohort.json')
    paths = [args.output.with_name(args.output.stem+f'-batch-{i+1}.json') for i in range(2)]
    if any(p.exists() for p in [cohort_path,*paths]):
        raise ValueError('refusing to overwrite paired experiment')
    with sqlite3.connect(f'file:{args.db.as_posix()}?mode=ro',uri=True) as conn:
        cutoff = conn.execute('SELECT COALESCE(MAX(id),0) FROM attempts').fetchone()[0]
        inventory = conn.execute('''SELECT f.name,f.insn_count,t.name,f.is_leaf,f.addr FROM functions f
            JOIN tus t ON t.id=f.tu_id WHERE f.insn_count >= 16
            AND NOT EXISTS(SELECT 1 FROM attempts a WHERE a.func_addr=f.addr)''').fetchall()
    eligible, excluded = [], []
    exposed_names,exposed_addresses,history = historical_exposure(args.historical_db)
    exposure_excluded=[]
    for row in inventory:
        if row[0] in exposed_names or row[4] in exposed_addresses:
            exposure_excluded.append(row[0])
            continue
        try:
            agentrepair._refuse_frozen_heldout(project/'eval/sets',row[0])
        except ValueError:
            excluded.append(row[0])
            continue
        if row[2].startswith('build/src/'):
            eligible.append(row[:4])
    batches, counts, shortages = paired_selection(eligible,args.seed)
    if not all(batches):
        raise ValueError('empty paired development batch')
    receipt = {'kind':'preselected-paired-stratified-development', 'seed':args.seed,
        'attempt_cutoff':cutoff, 'inventory_sha256':hashlib.sha256(json.dumps(sorted(inventory)).encode()).hexdigest(),
        'selection':'one stable hash per domain x size x leaf/calling stratum per batch; zero prior attempts; both selected before execution',
        'size_bins':{'small':[16,64],'medium':[65,160],'large':[161,400],'huge':[401,None]},
        'eligible_by_stratum':counts, 'insufficient_strata':shortages,
        'excluded_heldout':sorted(excluded), 'batches':batches,
        'historical_exposure':history,'excluded_prior_exposure':sorted(exposure_excluded),
        'reference_bodies_used':False,'historical_seeds_used':False,
        'preflight_model_digest':model_identity,
        'budget':{'model_calls_per_visit':args.model_calls,'max_work_items_per_batch':args.max_work_items,
                  'timeout':args.timeout,'num_predict':args.num_predict}}
    agentrepair._atomic_json(cohort_path,receipt)
    print(json.dumps(receipt,indent=2),flush=True)
    for batch, path in zip(batches,paths):
        result = completion_campaign.run(project=project,repo=args.repo,db=args.db,
            state_path=path,functions=tuple(row['function'] for row in batch),
            model_calls=args.model_calls,max_work_items=args.max_work_items,
            timeout=args.timeout,num_predict=args.num_predict)
        print(json.dumps({'path':str(path),'status':result['status'],'summary':result.get('summary')}),flush=True)
        if result['status'] not in ('paused_budget','stalled_requires_new_strategy_or_evidence',
                                    'awaiting_integration','cohort_objects_exact','cohort_integrated'):
            raise RuntimeError('paired experiment stopped at controller boundary: '+result['status'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seed', default='20260905-campaign-gap-expansion-v1:')
    parser.add_argument('--model-calls', type=int, default=0)
    parser.add_argument('--max-work-items', type=int, default=16)
    parser.add_argument('--timeout', type=int, default=240)
    parser.add_argument('--num-predict', type=int, default=3000)
    parser.add_argument('--paired-stratified', action='store_true')
    parser.add_argument('--historical-db',type=Path,action='append',default=[],
                        help='read-only prior extraction histories; exclude attempts across epochs')
    args = parser.parse_args()
    if args.model_calls < 0 or min(args.max_work_items, args.timeout, args.num_predict) <= 0:
        parser.error('budgets must be positive; model-calls may be zero')
    with sqlite3.connect(f'file:{args.db.as_posix()}?mode=ro',uri=True) as conn:
        versions=[json.loads(row[0]) for row in conn.execute('SELECT tool_versions FROM extraction')]
    if any(v.get('range_policy') for v in versions) and not args.historical_db:
        parser.error('range-corrected extraction requires --historical-db to preserve prior exposure')
    if args.historical_db and not args.paired_stratified:
        parser.error('--historical-db currently requires --paired-stratified; refusing to ignore exposure')
    project = Path.cwd()
    cohort_path = args.output.with_name(args.output.stem + '.cohort.json')
    if args.output.exists() or cohort_path.exists():
        raise ValueError('refusing to overwrite campaign or selection receipt')
    model_identity = frozen_wavefront.model_digest(llm.host(), 'gpt-oss:20b') if args.model_calls else None
    if args.paired_stratified:
        return run_paired(args,project,model_identity)
    with sqlite3.connect(f'file:{args.db.as_posix()}?mode=ro', uri=True) as conn:
        cutoff = conn.execute('SELECT COALESCE(MAX(id),0) FROM attempts').fetchone()[0]
        rows = conn.execute('''SELECT f.name,f.insn_count,t.name FROM functions f
            JOIN tus t ON t.id=f.tu_id WHERE f.insn_count BETWEEN 65 AND 400
            AND NOT EXISTS(SELECT 1 FROM attempts a WHERE a.func_addr=f.addr)''').fetchall()
    seed = args.seed
    groups = {}
    excluded = []
    for name, count, tu in rows:
        try:
            agentrepair._refuse_frozen_heldout(project/'eval/sets', name)
        except ValueError:
            excluded.append(name)
            continue
        if not tu.startswith('build/src/'):
            continue  # this panel tests ordinary C intake; hardware tracked separately
        stratum = ('sdk' if '/ultra/' in tu else 'game') + ('_medium' if count <= 160 else '_large')
        groups.setdefault(stratum, []).append({'function': name, 'instructions': count, 'tu': tu,
            'selection_hash': hashlib.sha256((seed + name).encode()).hexdigest(), 'stratum': stratum})
    selected = [row for key in sorted(groups)
                for row in sorted(groups[key], key=lambda row: row['selection_hash'])[:2]]
    if len(selected) != 8 or len(groups) != 4:
        raise ValueError('insufficient untouched eligible functions for fixed strata')
    receipt = {'kind': 'preselected-development-expansion', 'seed': seed,
        'selection': 'two smallest stable hashes per game/SDK x 65-160/161-400 instructions; zero prior attempts',
        'attempt_cutoff': cutoff, 'excluded_heldout': sorted(excluded),
        'eligible_by_stratum': {key: len(value) for key, value in groups.items()}, 'dev': selected,
        'reference_bodies_used': False, 'historical_seeds_used': False,
        'preflight_model_digest': model_identity,
        'budget': {'model_calls_per_visit': args.model_calls, 'max_work_items': args.max_work_items,
                   'timeout': args.timeout, 'num_predict': args.num_predict}}
    agentrepair._atomic_json(cohort_path, receipt)
    print(json.dumps(receipt, indent=2), flush=True)
    result = completion_campaign.run(project=project, repo=args.repo, db=args.db,
        state_path=args.output, functions=tuple(row['function'] for row in selected),
        model_calls=args.model_calls, max_work_items=args.max_work_items,
        timeout=args.timeout, num_predict=args.num_predict)
    print(json.dumps({'status': result['status'], 'summary': result.get('summary')}, indent=2), flush=True)


if __name__ == '__main__':
    main()
