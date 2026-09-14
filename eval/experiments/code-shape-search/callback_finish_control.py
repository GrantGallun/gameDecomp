import json,sys,sqlite3,time,re,itertools
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from solver import workspace,callback_control_alternatives,principle_variants

out=ROOT/('eval/results/'+(sys.argv[1] if len(sys.argv)>1 else 'callback-finish-control-v1'))
out.mkdir(exist_ok=True)
source=(ROOT/(sys.argv[2] if len(sys.argv)>2 else 'eval/results/swarm-final/createCallbackTaskPreservingArgs.c')).read_text()
repo=Path('/home/grant/decomp/sbk1'); ws=repo/'nonmatchings/createCallbackTaskPreservingArgs'
conn=sqlite3.connect(ROOT/'eval/results/direct-source-semantic-cohort-v1/attempts.sqlite')
variants=[principle_variants.Variant('baseline',source)]+callback_control_alternatives.candidates(source,'createCallbackTaskPreservingArgs',40)
if len(sys.argv)>3 and sys.argv[3]=='allocation':
    allocation='''    gFreeCallbackTaskCount--;
    idx = gFreeCallbackTaskCount;
    insertAfter = &gCallbackTaskActiveListSentinel;
    newTask = gFreeCallbackTaskPool[idx];'''
    blocks=[
        'insertAfter = &gCallbackTaskActiveListSentinel;\n    newTask = gFreeCallbackTaskPool[--gFreeCallbackTaskCount];',
        'newTask = gFreeCallbackTaskPool[--gFreeCallbackTaskCount];\n    insertAfter = &gCallbackTaskActiveListSentinel;',
        'gFreeCallbackTaskCount--;\n    insertAfter = &gCallbackTaskActiveListSentinel;\n    newTask = gFreeCallbackTaskPool[gFreeCallbackTaskCount];',
        'gFreeCallbackTaskCount--;\n    newTask = gFreeCallbackTaskPool[gFreeCallbackTaskCount];\n    insertAfter = &gCallbackTaskActiveListSentinel;',
        'idx = --gFreeCallbackTaskCount;\n    insertAfter = &gCallbackTaskActiveListSentinel;\n    newTask = gFreeCallbackTaskPool[idx];',
        'idx = gFreeCallbackTaskCount - 1;\n    gFreeCallbackTaskCount = idx;\n    insertAfter = &gCallbackTaskActiveListSentinel;\n    newTask = gFreeCallbackTaskPool[idx];',
    ]
    for i,block in enumerate(blocks):
        for clean in (False,True):
            code=source.replace(allocation,'    '+block)
            if clean:
                if len(re.findall(r'\bidx\b',code))==1: code=code.replace('    u16 idx;\n','')
                else: code=code.replace('    u16 idx;','    s32 idx;')
            variants.append(principle_variants.Variant(f'allocation-{i}-clean{clean}',code))
    fields=['newTask->isActive = 1;','newTask->callback = callback;','newTask->type = type;','newTask->priority = (u16)priority;']
    old='\n    '.join(fields)
    for i,order in enumerate(itertools.permutations(fields)):
        if list(order)!=fields:
            variants.append(principle_variants.Variant(f'field-order-{i}',source.replace(old,'\n    '.join(order))))
rows=[]
for i,v in enumerate(variants):
    name=f'createCallbackTaskPreservingArgs_control_{time.time_ns()}'
    a=workspace.score(ws,repo,name,v.source,conn,'createCallbackTaskPreservingArgs')
    (out/f'{i}.c').write_text(v.source); (out/f'{i}.diff').write_text(a.diff)
    row=dict(index=i,label=v.label,score=a.score,compiled=a.compiled,exact=a.exact,attempt=a.receipt_id,artifact=name)
    rows.append(row); (out/'summary.json').write_text(json.dumps(rows,indent=2)); print(row,flush=True)
