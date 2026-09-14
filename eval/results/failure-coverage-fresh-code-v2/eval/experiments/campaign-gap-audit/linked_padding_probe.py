"""Replay recorded callee admission inputs; no source/model/game mutations."""
import hashlib
import argparse
import json
from pathlib import Path

from eval import agentrepair
from solver import callee_execution, linked_callee


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    root=Path('/mnt/c/Code/gameDecomp')
    repo=Path('/home/grant/decomp/sbk1')
    output=args.out or root/'eval/results/linked-padding-admission-v1.json'
    if output.exists():
        raise ValueError('refusing to overwrite receipt')
    rows=[]
    for path in sorted((root/'eval/results/failure-coverage-fixes-replay-v2-artifacts').glob('*.json')):
        receipt=json.loads(path.read_text())
        panel=receipt.get('semantic_panel') or {}
        contracts=panel.get('call_contracts') or (receipt['result'].get('semantic_validation') or {}).get('call_contracts',{})
        if not contracts:
            continue
        environment,report=callee_execution.load_binary_leaves(repo,contracts,contracts)
        rows.append({'function':receipt['config']['function'],'input_receipt':str(path),
            'input_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'source_sha256':receipt['result']['best_source_sha256'],
            'contracts':contracts,'previous':panel.get('callee_admission'),
            'current':report,'environment':environment.manifest()})
    if not rows:
        raise ValueError('no bound admission inputs found')
    result={'kind':'recorded-callee-admission-replay','rows':rows,'model_calls':0,
        'reference_bodies_used':False,'integration_requested':False,
        'implementation_sha256':{str(Path(module.__file__)):hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in (callee_execution,linked_callee)},
        'scope':'admission only; newly admitted callees require caller semantic replay; not semantic or exactness proof'}
    agentrepair._atomic_json(output,result)
    print(json.dumps({'receipt':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
        'rows':[{'function':r['function'],'current':[(c['callee'],c['status'],c.get('linked_decline')) for c in r['current']]} for r in rows]}))


if __name__=='__main__':
    main()
