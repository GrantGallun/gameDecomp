"""Frozen zero-model activation replay of the two completed fresh cohorts.

This is replay of exposed development failures, NOT unseen-target evaluation.
No original receipts, immutable baseline, heldout memberships or game C change.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import traceback


ROOT=Path('/mnt/c/Code/gameDecomp')
REPO=Path('/home/grant/decomp/sbk1')
PREFIX='failure-coverage-fixes-replay-v1'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def selections(paths):
    rows=[]
    seen=set()
    for path in paths:
        state=json.loads(path.read_text(encoding='utf-8'))
        if state.get('inflight') or state['status']!='paused_budget':
            raise ValueError('requires terminal budget checkpoint without inflight work')
        for name,node in sorted(state['nodes'].items()):
            if name in seen:
                raise ValueError('cohorts overlap')
            seen.add(name)
            row={'function':name,'checkpoint':str(path),'checkpoint_sha256':sha(path),
                 'source':node.get('source'),'source_sha256':node.get('source_sha256'),
                 'historical_attempt_id':node.get('attempt_id'),'historical_status':node['status'],
                 'historical_blocker':node.get('blocker')}
            if row['source']:
                if sha(Path(row['source']))!=row['source_sha256']:
                    raise ValueError('historical source hash mismatch: '+name)
            elif node['status']!='parked' or not row['historical_blocker']:
                raise ValueError('missing source without an explicit parked boundary: '+name)
            rows.append(row)
    if len(rows)!=16:
        raise ValueError('expected the two preselected eight-function cohorts')
    return rows


def parked_report(row,db):
    import yaml
    from solver import sdk_intake
    config=yaml.safe_load((REPO/'snowboardkids.yaml').read_text())
    rom=(REPO/config['options']['target_path']).read_bytes()
    if hashlib.sha1(rom).hexdigest()!=config['sha1']:
        raise ValueError('ROM identity changed')
    with sqlite3.connect(f'file:{db.as_posix()}?mode=ro',uri=True) as conn:
        address,size=conn.execute('SELECT addr,size FROM functions WHERE name=?',(row['function'],)).fetchone()
    offset=sdk_intake.rom_offset(config,address,size,len(rom))
    raw=rom[offset:offset+size]
    if hashlib.sha256(raw).hexdigest()!=row['historical_blocker']['slice_sha256']:
        raise ValueError('parked target ROM extent changed')
    with tempfile.TemporaryDirectory(prefix='frozen-parked-audit-') as folder:
        binary=Path(folder)/'slice.bin'
        binary.write_bytes(raw)
        dump=subprocess.run(['mips-linux-gnu-objdump','-D','-z','-b','binary','-m','mips:4300',
            '-EB',f'--adjust-vma={address}',str(binary)],check=True,capture_output=True,text=True,timeout=30).stdout
    return {**sdk_intake.classify(sdk_intake.instructions(dump,address,size)),
        'address':address,'size':size,'rom_offset':offset,'slice_sha256':hashlib.sha256(raw).hexdigest(),
        'scope':'fresh raw-ROM instruction inventory; parking alone is not causal closure'}


class NoModel:
    provider_id='forbidden-zero-model-frozen-replay'
    def __getattr__(self,name):
        raise AssertionError('model access forbidden in deterministic replay: '+name)


def worker(manifest_path):
    from eval import agentrepair, frozen_wavefront
    manifest=json.loads(manifest_path.read_text())
    snapshot=Path(manifest['snapshot'])
    if Path.cwd().resolve()!=snapshot.resolve():
        raise ValueError('worker must import from frozen snapshot')
    pins=manifest['pins']
    db=Path(manifest['db'])
    output=ROOT/'eval/results'/f'{PREFIX}.json'
    if output.exists():
        raise ValueError('refusing to overwrite replay output')
    receipt={'kind':'frozen-final-source-fix-activation-replay','manifest':str(manifest_path),
        'manifest_sha256':sha(manifest_path),'status':'running','rows':[],
        'reference_bodies_used':False,'integration_requested':False,'model_calls':0,
        'scope':'exposed development source replay; not unseen transfer or whole-game decompilation'}
    agentrepair._atomic_json(output,receipt)
    for original in manifest['selection']:
        frozen_wavefront.verify_files(pins)
        name=original['function']
        agentrepair._refuse_frozen_heldout(snapshot/'eval/sets',name)
        row={**original}
        try:
            if not original['source']:
                row.update(outcome='intake_boundary_rechecked',intake=parked_report(original,db))
            else:
                source_path=Path(original['source'])
                if sha(source_path)!=original['source_sha256']:
                    raise ValueError('selected source changed')
                artifact=ROOT/'eval/results'/f'{PREFIX}-artifacts'/f'{name}.json'
                result=agentrepair.run(repo=REPO,db=db,function=name,
                    source=source_path.read_text(encoding='utf-8'),source_parent_attempt_id=None,
                    out=artifact,best_source_out=artifact.with_suffix('.best.c'),
                    model='forbidden',endpoint='http://127.0.0.1:1',draws=1,depth=1,beam=3,
                    max_calls=0,timeout=1,think='low',num_thread=1,temperature=0,num_predict=1,
                    seed=20260906,cache_dir=None,verbose=False,provider=NoModel(),
                    deterministic_budget=8,deterministic_depth=2,resilient=True,
                    semantic_cases=64,semantic_steps=10000)['result']
                if result['calls_attempted']:
                    raise AssertionError('unexpected model calls')
                semantic=result.get('semantic_validation') or {}
                row.update(outcome='evaluated',receipt=str(artifact),receipt_sha256=sha(artifact),
                    best_attempt_id=result['best_attempt_id'],best_source_sha256=result['best_source_sha256'],
                    exact=result['exact'],residual=result['best_residual'],
                    semantic_status=semantic.get('status'),semantic_counts=semantic.get('counts'),
                    semantic_debt=semantic.get('debt'),deterministic_log=result.get('deterministic_log'))
        except Exception as exc:
            row.update(outcome='execution_error',error_type=type(exc).__name__,error=str(exc),
                       traceback=traceback.format_exc())
        frozen_wavefront.verify_files(pins)
        receipt['rows'].append(row)
        agentrepair._atomic_json(output,receipt)
        print(json.dumps({k:row.get(k) for k in ('function','outcome','exact','semantic_status','semantic_counts','error')}),flush=True)
    receipt['status']='finished_with_errors' if any(r['outcome']=='execution_error' for r in receipt['rows']) else 'finished'
    receipt['immutable_baseline_unchanged']=sha(Path(manifest['baseline']))==manifest['baseline_sha256']
    if not receipt['immutable_baseline_unchanged']:
        raise ValueError('immutable baseline changed')
    agentrepair._atomic_json(output,receipt)


def main():
    global PREFIX
    parser=argparse.ArgumentParser()
    parser.add_argument('--worker',type=Path)
    parser.add_argument('--version',type=int,default=1)
    args=parser.parse_args()
    if args.version<1:
        raise ValueError('version must be positive')
    PREFIX=f'failure-coverage-fixes-replay-v{args.version}'
    if args.worker:
        return worker(args.worker)
    from eval import agentrepair, completion_campaign, frozen_wavefront
    snapshot=ROOT/f'eval/results/failure-coverage-fixes-code-v{args.version}'
    db=ROOT/f'eval/results/kb-sbk1-fixes-replay-v{args.version}.sqlite'
    manifest_path=ROOT/'eval/results'/f'{PREFIX}-preflight.json'
    if any(p.exists() for p in (snapshot,db,manifest_path)):
        raise ValueError('refusing to overwrite frozen experiment')
    baseline=ROOT/'eval/results/kb-sbk1-rom-ranges-v1.sqlite'
    baseline_sha=sha(baseline)
    if baseline_sha!='9f6ce8437a3a5c9657dfa47174c989b2ea9a553bed3cc41b12b12d3a407d8d5a':
        raise ValueError('immutable baseline changed')
    selected=selections([ROOT/'eval/results'/f'failure-coverage-fresh-paired-v1-batch-{i}.json' for i in (1,2)])
    for row in selected:
        agentrepair._refuse_frozen_heldout(ROOT/'eval/sets',row['function'])
    snapshot.mkdir()
    for package in ('solver','eval','kb','patterns','tools','miner','tests'):
        shutil.copytree(ROOT/package,snapshot/package,ignore=shutil.ignore_patterns('results','__pycache__','.cache'))
    shutil.copy2(ROOT/'pytest.ini',snapshot/'pytest.ini')
    shutil.copy2(baseline,db)
    with sqlite3.connect(f'file:{db.as_posix()}?mode=ro',uri=True) as conn:
        integrity=conn.execute('PRAGMA integrity_check').fetchone()[0]
        counts={name:conn.execute('SELECT COUNT(*) FROM '+name).fetchone()[0]
                for name in ('functions','evidence','attempts','inference')}
    if sha(db)!=baseline_sha or integrity!='ok' or counts!={
            'functions':2113,'evidence':72041,'attempts':0,'inference':0}:
        raise ValueError('fresh replay DB preflight failed')
    pins=completion_campaign._pins(snapshot,REPO)
    pins.update(frozen_wavefront.file_hashes([Path(row[key]) for row in selected
        for key in ('checkpoint','source') if row.get(key)]))
    manifest={'kind':'frozen-fix-replay-preflight','snapshot':str(snapshot),'db':str(db),
        'baseline':str(baseline),'baseline_sha256':baseline_sha,'copy_sha256':sha(db),
        'selection':selected,'pins':pins,'integrity':integrity,'counts':counts,
        'model_calls':0,'integration_requested':False,
        'scope':'replay original final sources; development hints/refinements are not injected'}
    agentrepair._atomic_json(manifest_path,manifest)
    subprocess.run([sys.executable,'-m','eval.experiments.campaign-gap-audit.replay_fresh_fixes_v1',
                    '--version',str(args.version),'--worker',str(manifest_path)],cwd=snapshot,check=True)


if __name__=='__main__':
    main()
