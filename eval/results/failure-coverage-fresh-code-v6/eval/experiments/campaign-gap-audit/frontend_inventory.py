"""Source-bound compiler/frontend inventory; never promote semantic status."""
import argparse
import hashlib
import json
from pathlib import Path
from eval import agentrepair


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(root, versions=(3,4)):
    results=root/'eval/results'
    rows={}
    checkpoints={}
    for version in versions:
        for batch in (1,2):
            path=results/f'failure-coverage-fresh-paired-v{version}-batch-{batch}.json'
            state=json.loads(path.read_text())
            if state.get('inflight'): raise ValueError('campaign still active')
            checkpoints[path.name]=digest(path)
            for name,node in state['nodes'].items():
                r=node.get('residual') or {}
                if node['status']=='parked' or not node.get('source_sha256'):
                    continue
                if r.get('compiled') and (r.get('frontend') or {}).get('passed'):
                    continue
                rows[name]={'function':name,'checkpoint':path.name,'checkpoint_sha256':digest(path),
                    'original_source_sha256':node['source_sha256'],
                    'compiler_error':r.get('compiler_error_signature'),
                    'frontend_errors':[line for line in (r.get('frontend') or {}).get('diagnostics','').splitlines() if 'error:' in line],
                    'compilation_unblocked_evidence':[]}
    probe=results/'frontend-v3-replay-v2.json'
    data=json.loads(probe.read_text())
    for p in data['checkpoints']:
        name=Path(p['path']).name
        if checkpoints.get(name)!=p['sha256']: raise ValueError('probe checkpoint mismatch')
    for row in data['rows']:
        item=rows.get(row['function'])
        if not item or not row.get('probes'): continue
        if row.get('original_source_sha256')!=item['original_source_sha256']:
            raise ValueError('compile probe input mismatch')
        for child in row['probes']:
            if child['compiled'] and (child.get('frontend') or {}).get('passed'):
                item['compilation_unblocked_evidence'].append({'receipt':probe.name,
                    'receipt_sha256':digest(probe),'attempt_id':child['attempt_id'],
                    'candidate_sha256':child['source_sha256'],'scope':'compile-only probe'})
    paths={path for version in versions for path in results.glob(f'v{version}-*-replay-v*.json')}
    for path in sorted(paths):
        inputs=path.with_name(path.stem+'-inputs.json')
        if not inputs.exists(): continue
        binding=json.loads(inputs.read_text())
        function=binding.get('function')
        if function not in rows: continue
        item=rows[function]
        if Path(binding.get('checkpoint','')).name!=item['checkpoint'] or binding.get('checkpoint_sha256')!=item['checkpoint_sha256']:
            raise ValueError('entry replay checkpoint mismatch')
        if binding.get('source_sha256')!=item['original_source_sha256']:
            raise ValueError('entry replay input mismatch')
        replay=json.loads(path.read_text()).get('result',{})
        residual=replay.get('best_residual') or {}
        if residual.get('compiled') and (residual.get('frontend') or {}).get('passed'):
            source=path.with_suffix('.best.c')
            if not source.exists() or digest(source)!=replay['best_source_sha256']:
                raise ValueError('entry replay best source mismatch')
            item['compilation_unblocked_evidence'].append({'receipt':path.name,
                'receipt_sha256':digest(path),'input_receipt_sha256':digest(inputs),
                'attempt_id':replay['best_attempt_id'],'candidate_sha256':replay['best_source_sha256'],
                'semantic_status':(replay.get('semantic_validation') or {}).get('status'),
                'scope':'repair-entry replay; compilation only counted here'})
    for row in rows.values():
        row['status']='compilation_unblocked' if row['compilation_unblocked_evidence'] else 'unresolved'
    return {'kind':'source-bound-frontend-inventory','original_blockers':len(rows),
        'remaining':sum(row['status']=='unresolved' for row in rows.values()),
        'rows':list(rows.values()),'checkpoints':checkpoints,
        'scope':'compiler/frontend recovery only; original campaign results unchanged; no semantic or exact promotion'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--version',type=int,required=True)
    parser.add_argument('--campaign-versions',type=int,nargs='+',default=[3,4,5])
    args=parser.parse_args()
    root=Path.cwd()
    out=root/f'eval/results/frontend-inventory-v{args.version}.json'
    if args.version<1 or out.exists(): raise ValueError('positive unused version required')
    if any(v<1 for v in args.campaign_versions) or len(set(args.campaign_versions))!=len(args.campaign_versions):
        raise ValueError('unique positive campaign versions required')
    result=build(root,tuple(args.campaign_versions))
    agentrepair._atomic_json(out,result)
    print({k:result[k] for k in ('original_blockers','remaining')})


if __name__=='__main__': main()
