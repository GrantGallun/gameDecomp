"""Bounded zero-model replay of an immutable remaining-frontend inventory."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from eval import agentrepair


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory',type=Path,required=True)
    parser.add_argument('--version',type=int,required=True)
    args=parser.parse_args()
    if args.version<1:raise ValueError('positive version required')
    root=args.inventory.parent
    out=root/f'frontend-remaining-replay-batch-v{args.version}.json'
    if out.exists():raise ValueError('refusing to overwrite batch receipt')
    data=json.loads(args.inventory.read_text())
    rows=[r for r in data['rows'] if r['status']!='compilation_unblocked']
    jobs=[]
    for row in rows:
        checkpoint=root/row['checkpoint']
        if hashlib.sha256(checkpoint.read_bytes()).hexdigest()!=row['checkpoint_sha256']:
            raise ValueError('checkpoint hash changed')
        version='v4' if '-v4-' in row['checkpoint'] else 'v3'
        result=root/f'{version}-frontend-batch{args.version}-{row["function"]}-replay-v1.json'
        if result.exists() or result.with_name(result.stem+'-inputs.json').exists():
            raise ValueError('refusing to reuse partial or completed child replay')
        jobs.append((row,checkpoint,result))
    report={'kind':'remaining-frontend-replay-batch','status':'running',
        'inventory_sha256':hashlib.sha256(args.inventory.read_bytes()).hexdigest(),
        'model_calls':0,'rows':[],'scope':'exposed-function replay, not fresh transfer or semantic proof'}
    agentrepair._atomic_json(out,report)
    for row,checkpoint,result in jobs:
        command=[sys.executable,'-m','eval.experiments.campaign-gap-audit.replay_checkpoint_source',
                 '--checkpoint',str(checkpoint),'--function',row['function'],'--out',str(result),'--compile-only']
        child=subprocess.run(command,capture_output=True,text=True)
        entry={'function':row['function'],'receipt':str(result),'returncode':child.returncode}
        if child.returncode:
            entry['error']=child.stderr[-6000:]
            report['rows'].append(entry);report['status']='child_failed'
            agentrepair._atomic_json(out,report)
            raise RuntimeError('child replay failed; receipt retained, no automatic restart')
        r=json.loads(result.read_text())['result'];residual=r['best_residual']
        entry.update(compiled=residual['compiled'],frontend_passed=residual.get('frontend',{}).get('passed'),
            semantic_status=(r.get('semantic_validation') or {}).get('status'),
            source_sha256=r['best_source_sha256'],normalizations=r['normalization_candidates'])
        report['rows'].append(entry);agentrepair._atomic_json(out,report)
        print(json.dumps(entry),flush=True)
    report['status']='complete';agentrepair._atomic_json(out,report)


if __name__=='__main__':main()
