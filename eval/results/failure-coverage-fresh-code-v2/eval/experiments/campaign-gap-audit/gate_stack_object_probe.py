"""Assisted header/binary-bound stack-object reconstruction, not generic recovery."""
import hashlib
import importlib
import json
from pathlib import Path
import re
from eval import agentrepair
from solver import type_constraints


def main():
    root=Path('/mnt/c/Code/gameDecomp')
    repo=Path('/home/grant/decomp/sbk1')
    function='renderCourseGateObject'
    parent=root/'eval/results/gate-partial-stores-v1.json'
    previous=json.loads(parent.read_text())
    source=Path(previous['result']['best_source_path']).read_text(encoding='utf-8')
    if hashlib.sha256(source.encode()).hexdigest()!=previous['result']['best_source_sha256']:
        raise ValueError('source changed')
    output=root/'eval/results/gate-stack-object-v1.json'
    inputs=output.with_name(output.stem+'-inputs.json')
    if output.exists() or inputs.exists():
        raise ValueError('refusing to overwrite experiment')
    ws=repo/'nonmatchings'/function
    target=json.loads((ws/'.compiler-target.json').read_text())['target']
    measured=type_constraints.measure(repo,ws,source,function,target)
    fields=measured['layouts'].get('Transform3D',[])
    if not all(any(f['member']=='translation.'+name and f['offset']==offset and
            f['width']==4 and f['owner_size']==32 for f in fields)
            for name,offset in [('x',20),('y',24),('z',28)]):
        raise ValueError('expected compiler-measured transform translation missing')
    changes=[('    s16 sp58;','    Transform3D sp58;'),
        ('makeFixedRotationZY(&sp58,','makeFixedRotationZY(sp58.rotation,')]
    candidate=source
    for before,after in changes:
        if candidate.count(before)!=1:
            raise ValueError('ambiguous object edit')
        candidate=candidate.replace(before,after)
    for local,field in [('sp6C','x'),('sp70','y'),('sp74','z')]:
        declaration='    s32 '+local+';\n'
        if candidate.count(declaration)!=1:
            raise ValueError('missing split translation local')
        candidate=candidate.replace(declaration,'')
        candidate,count=re.subn(r'\b'+local+r'\b','sp58.translation.'+field,candidate)
        if count!=2:
            raise ValueError('unexpected split translation uses')
        changes.append((local,'sp58.translation.'+field))
    agentrepair._atomic_json(inputs,{'parent_receipt':str(parent),
        'parent_sha256':hashlib.sha256(parent.read_bytes()).hexdigest(),
        'source_sha256':previous['result']['best_source_sha256'],
        'candidate_sha256':hashlib.sha256(candidate.encode()).hexdigest(),
        'changes':changes,'measurement':measured,
        'scope':'assisted object/overlapping-slot reconstruction; no reference bodies/integration',
        'binary_observation':'caller passes stack+0x58 and copies eight words through0x74; translation occupies0x6c/70/74'})
    provider=importlib.import_module('eval.experiments.campaign-gap-audit.replay_fresh_fixes_v1').NoModel()
    result=agentrepair.run(repo=repo,db=root/'eval/results/kb-sbk1-range-replay-v1.sqlite',
        function=function,source=candidate,source_parent_attempt_id=previous['result']['best_attempt_id'],
        out=output,best_source_out=output.with_suffix('.best.c'),model='zero-model-assisted',endpoint='http://127.0.0.1:1',
        draws=1,depth=1,beam=3,max_calls=0,timeout=1,think='low',num_thread=1,temperature=0,num_predict=1,
        seed=20260906,cache_dir=None,verbose=False,provider=provider,resilient=True,
        semantic_cases=64,semantic_steps=10000)['result']
    print(json.dumps({'attempt':result['best_attempt_id'],'compiled':result['best_residual']['compiled'],
        'exact':result['exact'],'score':result['best_residual']['weighted_progress_score'],
        'semantic_status':(result.get('semantic_validation') or {}).get('status'),
        'counts':(result.get('semantic_validation') or {}).get('counts')}))


if __name__=='__main__':
    main()
