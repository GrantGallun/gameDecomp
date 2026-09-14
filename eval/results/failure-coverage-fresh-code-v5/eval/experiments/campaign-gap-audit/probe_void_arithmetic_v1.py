"""Compile-only mechanism test on two immutable fresh-v3 failed worker sources."""
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import argparse
from eval import agentrepair
from solver import repair_context, workspace


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--version',type=int,default=1)
    args=parser.parse_args()
    if args.version<1: raise ValueError('positive version required')
    root=Path('/mnt/c/Code/gameDecomp')
    repo=Path('/home/grant/decomp/sbk1')
    out=root/f'eval/results/void-arithmetic-transfer-v{args.version}.json'
    if out.exists():
        raise ValueError('refusing to overwrite probe')
    jobs=[('recordRaceReplayInputFrame','1788755391950004414'),
          ('saveRaceRecordReplayData','1788755460594400798')]
    rows=[]
    with sqlite3.connect(root/'eval/results/kb-sbk1-range-replay-v1.sqlite',timeout=120) as conn:
        for function,stamp in jobs:
            agentrepair._refuse_frozen_heldout(root/'eval/sets',function)
            receipt=root/f'eval/results/failure-coverage-fresh-paired-v3-batch-1-artifacts/{stamp}-{function}.json'
            parent=json.loads(receipt.read_text())
            source=Path(parent['best_source_path']).read_text(encoding='utf-8')
            if hashlib.sha256(source.encode()).hexdigest()!=parent['best_source_sha256']:
                raise ValueError('completed worker source changed')
            repair_context.definition(source,function)
            ws=workspace.bootstrap(repo,function)
            target_hash=hashlib.sha256((ws/'target.o').read_bytes()).hexdigest()
            tag=function+'_void_probe_'+str(time.time_ns())
            base=workspace.score(ws,repo,tag,source,conn=conn,func=function,
                strategy='void-arithmetic-probe-root',extra={'parent_worker':str(receipt),
                'worker_sha256':hashlib.sha256(receipt.read_bytes()).hexdigest()})
            candidates=dict(repair_context.normalize(source,base.compiler_stderr,function))
            candidate=candidates.get('void-local-byte-arithmetic')
            if candidate is None:
                raise ValueError('general mechanism did not activate: '+function)
            child=workspace.score(ws,repo,tag+'_child',candidate,conn=conn,func=function,
                strategy='void-arithmetic-probe-child',parent_attempt_id=base.receipt_id,
                relation='compiler-normalization',action='void-local-byte-arithmetic')
            unchanged=target_hash==hashlib.sha256((ws/'target.o').read_bytes()).hexdigest()
            rows.append({'function':function,'original_worker':str(receipt),
                'worker_sha256':hashlib.sha256(receipt.read_bytes()).hexdigest(),
                'original_source_sha256':parent['best_source_sha256'],
                'root_attempt_id':base.receipt_id,'child_attempt_id':child.receipt_id,
                'child_source_sha256':hashlib.sha256(candidate.encode()).hexdigest(),
                'compiled':child.compiled,'frontend':child.frontend,'score':child.score,
                'compiler_stderr':child.compiler_stderr,'target_sha256':target_hash,
                'target_unchanged':unchanged,'semantic_status':'not_tested'})
            if not unchanged:
                raise ValueError('target changed during probe')
    agentrepair._atomic_json(out,{'kind':'two-source-void-arithmetic-probe','rows':rows,
        'model_calls':0,'integration_requested':False,'reference_bodies_used':False,
        'scope':'exposed-source compile-only hypothesis test, not autonomous or semantic acceptance'})
    print([{'function':r['function'],'compiled':r['compiled'],'frontend':r['frontend']['passed']} for r in rows])


if __name__=='__main__':
    main()
