"""Receipt audit and non-mutating replay to count actual callee activation."""
from collections import Counter
import hashlib
import json
from pathlib import Path

from eval import agentrepair
from solver import callee_execution, mips_differential as d, workspace


def main():
    root,repo=Path('/mnt/c/Code/gameDecomp'),Path('/home/grant/decomp/sbk1')
    output=root/'eval/results/stack-callee-summary-v2.json'
    if output.exists():
        raise ValueError('refusing to overwrite audited summary')
    paired=json.loads((root/'eval/results/stack-callee-replay-v3.json').read_text())
    rows=[]
    for pair in paired['pairs']:
        missing=[]
        edges=[]
        for side in ('target','candidate'):
            automatic=pair['semantic'][side+'_coverage']
            directed=pair['directed_cases'][side+'_coverage']
            absent = {r['instruction'] for r in automatic['missing_instructions']} & {r['instruction'] for r in directed['missing_instructions']}
            remaining = {(r['instruction'],r['outcome']) for r in automatic['unresolved_branch_edges']} & {(r['instruction'],r['outcome']) for r in directed['unresolved_branch_edges']}
            missing.append((side,sorted(absent)))
            edges.append((side,sorted(remaining)))
        counts=Counter(pair['semantic']['counts'])+Counter(pair['directed_cases']['counts'])
        rows.append({'label':pair['label'],'attempt_id':pair['attempt_id'],
            'source_sha256':pair['source_sha256'],'same_environment_combined_counts':dict(counts),
            'combined_missing_instructions':dict(missing),'combined_unresolved_edges':dict(edges)})
    transfers=[]
    for filename in ('callee-transfer-alLoadParam-v1.json','callee-transfer-ending-v1.json'):
        value=json.loads((root/'eval/results'/filename).read_text())
        name=value['config']['function']
        expected=value['result']['best_source_sha256']
        source=Path(value['result']['best_source_path']).read_text()
        assert hashlib.sha256(source.encode()).hexdigest()==expected
        env,admission=callee_execution.load_binary_leaves(repo,value['semantic_panel']['callee_environment']['leaves'])
        assert json.loads(json.dumps(env.manifest()))==value['semantic_panel']['callee_environment']
        ws=repo/'nonmatchings'/name
        artifact=None
        for file in sorted(ws.glob('*.semantic-lane.json'),key=lambda p:p.stat().st_mtime,reverse=True):
            recorded=json.loads(file.read_text())
            if recorded.get('source_sha256')==expected and recorded.get('panel_sha256')==value['semantic_panel']['panel_sha256']:
                artifact=file.with_name(file.name.removesuffix('.semantic-lane.json')+'.o')
                break
        assert artifact and artifact.is_file(),name
        target=workspace.semantic_assembly((ws/'target_object_dump_normalized.s').read_text(),ws/'target.o')
        candidate=workspace.semantic_assembly(artifact.with_name(artifact.stem+'_object_dump_normalized.s').read_text(),artifact)
        cases=tuple(d.TestCase(**case) for case in value['semantic_panel']['cases'])
        runs=d.run_suite(target,candidate,cases,call_arities=value['semantic_panel']['call_arities'],
            return_registers=tuple(value['semantic_panel']['abi']['return_registers']),callee_environment=env)
        transfers.append({'function':name,'source_sha256':expected,'attempt_id':value['result']['best_attempt_id'],
            'target_sha256':hashlib.sha256(target.encode()).hexdigest(),
            'candidate_sha256':hashlib.sha256(candidate.encode()).hexdigest(),
            'counts':dict(Counter(r.status for r in runs)),
            'concrete_calls':{side:dict(Counter(c['callee'] for r in runs for c in getattr(r,side).concrete_calls)) for side in ('target','candidate')}})
    agentrepair._atomic_json(output,{'kind':'stack-callee-receipt-and-activation-audit',
        'paired_receipt':'stack-callee-replay-v3.json','combined':rows,'transfers':transfers,
        'runner_sha256':hashlib.sha256(Path(d.__file__).read_bytes()).hexdigest(),
        'authority':'finite DEV tests only; __osBlockSum combined results assume explicit output environment',
        'model_calls':0,'new_exact_matches':0,'game_source_changed':False})
    print(json.dumps({'combined':rows,'transfers':transfers},indent=2))


if __name__=='__main__':
    main()
