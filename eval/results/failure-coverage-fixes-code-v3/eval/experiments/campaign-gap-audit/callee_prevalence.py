"""Zero-model activation audit over the already inspected 24+8 DEV cohort."""
import hashlib
import json
from pathlib import Path
import sqlite3

from eval import agentrepair
from solver import callee_execution, project_headers, stack_buffers, workspace


def main():
    root,repo = Path('/mnt/c/Code/gameDecomp'),Path('/home/grant/decomp/sbk1')
    output = root/'eval/results/stack-callee-prevalence-v1.json'
    if output.exists():
        raise ValueError('refusing to overwrite activation audit')
    rows,names,pins = [],set(),{}
    with sqlite3.connect('file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro',uri=True) as conn:
        for path in [root/'eval/results/autonomy-wavefront-24-v14.json',
                     root/'eval/results/autonomy-gap-expansion-8-v2.json']:
            pins[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            for name,node in json.loads(path.read_text())['nodes'].items():
                agentrepair._refuse_frozen_heldout(root/'eval/sets',name)
                row = {'function':name,'source_attempt_id':node.get('attempt_id')}
                if not node.get('attempt_id'):
                    rows.append({**row,'status':'no selected candidate'})
                    continue
                source = agentrepair._source_for_attempt(conn,node['attempt_id'],name)
                if hashlib.sha256(source.encode()).hexdigest() != node['source_sha256']:
                    raise ValueError('source hash changed: '+name)
                try:
                    assembly = workspace.target_asm(repo/'nonmatchings'/name,name)
                except (OSError,RuntimeError) as exc:
                    rows.append({**row,'status':'no target assembly','reason':str(exc)})
                    continue
                variants,report = stack_buffers.candidates(source,assembly,name)
                calls = project_headers.called_functions(assembly)
                names.update(calls)
                rows.append({**row,'status':'audited','stack_hypotheses':report,
                    'candidate_count':len(variants),'calls':calls})
    environment,admission = callee_execution.load_binary_leaves(repo,names)
    for row in rows:
        row['executable_callees'] = sorted(set(row.get('calls',())) & environment.leaves.keys())
    summary = {'functions':len(rows),'audited':sum(r['status']=='audited' for r in rows),
        'functions_with_stack_candidates':sum(bool(r.get('candidate_count')) for r in rows),
        'unique_executable_leaves':len(environment.leaves),
        'functions_with_executable_callees':sum(bool(r['executable_callees']) for r in rows)}
    agentrepair._atomic_json(output,{'kind':'development-activation-not-transfer-benchmark',
        'cohort_sha256':pins,'rows':rows,'callee_admission':admission,'summary':summary,
        'environment':environment.manifest(),'model_calls':0,'source_edits':0})
    print(json.dumps(summary),flush=True)
    print('stack activations:',[(r['function'],r['candidate_count']) for r in rows if r.get('candidate_count')],flush=True)


if __name__=='__main__':
    main()
