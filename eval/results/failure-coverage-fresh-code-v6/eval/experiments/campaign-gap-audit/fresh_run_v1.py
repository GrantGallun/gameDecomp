"""Freeze current code and launch two selection-history-filtered DEV cohorts."""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys


def options(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version',type=int,default=1)
    parser.add_argument('--model-calls',type=int,default=3)
    parser.add_argument('--per-stratum',type=int,default=1)
    parser.add_argument('--max-work-items',type=int,default=40)
    args=parser.parse_args(argv)
    if args.version < 1 or args.model_calls < 0 or min(args.per_stratum,args.max_work_items) < 1:
        parser.error('positive version and nonnegative model-call budget required')
    return args


def main():
    args=options()
    version=args.version
    root=Path('/mnt/c/Code/gameDecomp')
    snapshot=root/f'eval/results/failure-coverage-fresh-code-v{version}'
    baseline=root/'eval/results/kb-sbk1-rom-ranges-v1.sqlite'
    db=root/f'eval/results/kb-sbk1-fresh-cohorts-v{version}.sqlite'
    preflight=root/f'eval/results/fresh-cohorts-preflight-v{version}.json'
    if any(path.exists() for path in (snapshot,db,preflight)):
        raise ValueError('refusing to overwrite frozen experiment')
    digest=hashlib.sha256(baseline.read_bytes()).hexdigest()
    if digest!='9f6ce8437a3a5c9657dfa47174c989b2ea9a553bed3cc41b12b12d3a407d8d5a':
        raise ValueError('immutable ROM baseline changed')
    history=importlib.import_module('eval.experiments.campaign-gap-audit.exposure_inventory')
    selection=importlib.import_module('eval.experiments.campaign-gap-audit.run_expansion')
    paths,other,unreadable=history.inventory(root/'eval/results')
    if unreadable:
        raise ValueError('unreadable historical receipts')
    histories=[Path('/home/grant/decomp/kb-sbk1.sqlite'),
        root/'eval/results/kb-sbk1-range-replay-v1.sqlite',root/'eval/results/kb-sbk1-rom-cohorts-v1.sqlite']
    histories += sorted((root/'eval/results').glob('kb-sbk1-fresh-cohorts-v*.sqlite'))
    names,addresses,bindings=selection.historical_exposure(histories,paths)
    snapshot.mkdir()
    for name in ('solver','eval','kb','patterns','tools','miner','tests'):
        shutil.copytree(root/name,snapshot/name,ignore=shutil.ignore_patterns('results','__pycache__','.cache'))
    shutil.copy2(root/'pytest.ini',snapshot/'pytest.ini')
    shutil.copy2(baseline,db)
    with sqlite3.connect(f'file:{db.as_posix()}?mode=ro',uri=True) as conn:
        integrity=conn.execute('PRAGMA integrity_check').fetchone()[0]
        counts={name:conn.execute('SELECT COUNT(*) FROM '+name).fetchone()[0]
                for name in ('functions','evidence','attempts','inference')}
    if integrity!='ok' or counts!={'functions':2113,'evidence':72041,'attempts':0,'inference':0}:
        raise ValueError('fresh DB preflight failed')
    from eval import agentrepair,frozen_wavefront
    agentrepair._atomic_json(preflight,{'kind':'fresh-stratified-cohort-preflight',
        'version':version,'model_call_budget':args.model_calls,
        'per_stratum_per_batch':args.per_stratum,'max_work_items_per_batch':args.max_work_items,
        'baseline_sha256':digest,'copy_sha256':hashlib.sha256(db.read_bytes()).hexdigest(),
        'integrity':integrity,'counts':counts,'snapshot_files':frozen_wavefront.file_hashes(frozen_wavefront.code_paths(snapshot)),
        'history':bindings,'exposed_names':sorted(names),'exposed_addresses':sorted(addresses),
        'unrecognized_history_schemas':other,
        'scope':'fresh relative to recorded attempts and known selection schemas; external/unrecognized inspection not certified',
        'reference_bodies_used':False,'integration_requested':False})
    command=[sys.executable,'-m','eval.experiments.campaign-gap-audit.run_expansion',
        '--repo','/home/grant/decomp/sbk1','--db',str(db),'--output',
        str(root/f'eval/results/failure-coverage-fresh-paired-v{version}.json'),'--paired-stratified',
        '--seed',f'20260906-failure-coverage-fresh-paired-v{version}:', '--model-calls',str(args.model_calls),
        '--per-stratum',str(args.per_stratum),
        '--max-work-items',str(args.max_work_items),'--timeout','240','--num-predict','6000']
    for path in histories:
        command+=['--historical-db',str(path)]
    for path in paths:
        command+=['--historical-cohort',str(path)]
    print(json.dumps({'preflight':str(preflight),'exposed_names':len(names),'selection_receipts':len(paths)}),flush=True)
    subprocess.run(command,cwd=snapshot,check=True)


if __name__=='__main__':
    main()
