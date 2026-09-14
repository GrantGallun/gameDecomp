import hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from solver import workspace
ws=Path('/home/grant/decomp/sbk1/nonmatchings/createCallbackTaskPreservingArgs')
for suffix in ('v1','v2'):
    out=ROOT/f'eval/results/callback-inverse-joint-{suffix}'
    rows=json.loads((out/'scores.json').read_text())
    classes={}
    for index,row in enumerate(rows):
        if not row['compiled']: continue
        tag=row['tag']
        assembly=workspace.semantic_assembly((ws/f'{tag}_object_dump_normalized.s').read_text(),ws/f'{tag}.o')
        key=hashlib.sha256(assembly.encode()).hexdigest()
        classes.setdefault(key,[]).append(dict(index=index,label=row['label'],score=row['score']))
    audit=dict(candidates=len(rows),compiled=sum(r['compiled'] for r in rows),best_score=max(r['score'] for r in rows),
               distinct_interpreter_inputs=len(classes),classes=classes)
    (out/'diversity.json').write_text(json.dumps(audit,indent=2))
    print(suffix,{k:v for k,v in audit.items() if k!='classes'})
