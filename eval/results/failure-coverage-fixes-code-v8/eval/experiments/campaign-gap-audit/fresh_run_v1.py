"""Freeze current code and launch two selection-history-filtered DEV cohorts."""
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys


def main():
    root=Path('/mnt/c/Code/gameDecomp')
    snapshot=root/'eval/results/failure-coverage-fresh-code-v1'
    baseline=root/'eval/results/kb-sbk1-rom-ranges-v1.sqlite'
    db=root/'eval/results/kb-sbk1-fresh-cohorts-v1.sqlite'
    preflight=root/'eval/results/fresh-cohorts-preflight-v1.json'
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
        'baseline_sha256':digest,'copy_sha256':hashlib.sha256(db.read_bytes()).hexdigest(),
        'integrity':integrity,'counts':counts,'snapshot_files':frozen_wavefront.file_hashes(frozen_wavefront.code_paths(snapshot)),
        'history':bindings,'exposed_names':sorted(names),'exposed_addresses':sorted(addresses),
        'unrecognized_history_schemas':other,
        'scope':'fresh relative to recorded attempts and known selection schemas; external/unrecognized inspection not certified',
        'reference_bodies_used':False,'integration_requested':False})
    command=[sys.executable,'-m','eval.experiments.campaign-gap-audit.run_expansion',
        '--repo','/home/grant/decomp/sbk1','--db',str(db),'--output',
        str(root/'eval/results/failure-coverage-fresh-paired-v1.json'),'--paired-stratified',
        '--seed','20260906-failure-coverage-fresh-paired-v1:', '--model-calls','3',
        '--max-work-items','40','--timeout','240','--num-predict','6000']
    for path in histories:
        command+=['--historical-db',str(path)]
    for path in paths:
        command+=['--historical-cohort',str(path)]
    print(json.dumps({'preflight':str(preflight),'exposed_names':len(names),'selection_receipts':len(paths)}),flush=True)
    subprocess.run(command,cwd=snapshot,check=True)


if __name__=='__main__':
    main()
