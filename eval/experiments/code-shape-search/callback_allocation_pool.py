"""Targeted pool-count value-flow experiment; no production integration."""
import json,re,sqlite3,sys,time
from dataclasses import asdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from solver import workspace
name='createCallbackTaskPreservingArgs'
source=(ROOT/'eval/results/callback-inverse-final'/f'{name}.c').read_text()
out=ROOT/'eval/results/callback-allocation-pool-v1'
out.mkdir(exist_ok=True)
options=[]
old='''if (gFreeCallbackTaskCount == 0) return NULL;
    gFreeCallbackTaskCount--;
    idx = gFreeCallbackTaskCount;'''
for typ in ('u16','u32','s32'):
    for flow in ('idx-check','value-check','value-decrement','duplicate-decrement'):
        for signed in (False,True):
            code=source
            if flow=='idx-check':
                code=code.replace('u16 idx;',typ+' idx;')
                new='''idx = (u16)gFreeCallbackTaskCount;
    if (idx == 0) return NULL;
    --idx;
    gFreeCallbackTaskCount = idx;'''
            else:
                code=code.replace('u16 idx;','u16 idx;\n    '+typ+' available;')
                prefix='available = (u16)gFreeCallbackTaskCount;\n    if (available == 0) return NULL;\n    '
                tail={'value-check':'gFreeCallbackTaskCount--;\n    idx = gFreeCallbackTaskCount;',
                      'value-decrement':'--available;\n    gFreeCallbackTaskCount = available;\n    idx = available;',
                      'duplicate-decrement':'gFreeCallbackTaskCount = available - 1;\n    idx = available - 1;'}[flow]
                new=prefix+tail
            code=code.replace(old,new)
            if signed:
                code=code.replace('extern u16 gFreeCallbackTaskCount;','extern s16 gFreeCallbackTaskCount;')
                code=code.replace('idx = gFreeCallbackTaskCount;','idx = (u16)gFreeCallbackTaskCount;')
            options.append((f'count-flow:{typ}:{flow}:{signed}',code))
for subset in ('pool','dispatch','both'):
    for spelling in ('cast-minus','cast-assign'):
        code=source
        names=(['gFreeCallbackTaskCount'] if subset=='pool' else [f'gFreeCallbackTaskType{i}Count' for i in range(7)] if subset=='dispatch' else ['gFreeCallbackTaskCount']+[f'gFreeCallbackTaskType{i}Count' for i in range(7)])
        for variable in names:
            code=code.replace('extern u16 '+variable+';','extern s16 '+variable+';')
            code=code.replace('if ('+variable+' == 0)', 'if ((u16)'+variable+' == 0)')
            rhs=f'(u16){variable} - 1' if spelling=='cast-minus' else f'(u16)((u16){variable} - 1)'
            code=code.replace(variable+'--;',variable+' = '+rhs+';')
            code=code.replace('idx = '+variable+';','idx = (u16)'+variable+';')
        options.append((f'signed-backing:{subset}:{spelling}',code))
if '--final' in sys.argv:
    out=ROOT/'eval/results/callback-allocation-pool-v2'
    out.mkdir(exist_ok=True)
    options=[]
    allocation='''gFreeCallbackTaskCount--;
    idx = gFreeCallbackTaskCount;
    insertAfter = &gCallbackTaskActiveListSentinel;
    newTask = gFreeCallbackTaskPool[idx];'''
    for expr in ('idx = gFreeCallbackTaskCount - 1', 'idx = --gFreeCallbackTaskCount', 'idx = (u16)(gFreeCallbackTaskCount - 1)', 'idx = gFreeCallbackTaskCount--'):
        lookup='newTask = gFreeCallbackTaskPool[('+expr+')'+(' - 1' if expr.endswith('--') else '')+'];'
        tail='\n    gFreeCallbackTaskCount = idx;' if '--' not in expr else ''
        replacement='insertAfter = &gCallbackTaskActiveListSentinel;\n    '+lookup+tail
        options.append(('assignment-result:'+expr,source.replace(allocation,replacement)))
    for pointer in ('cur','insertAfter','newTask'):
        replacement=allocation.replace('newTask = gFreeCallbackTaskPool[idx];',f'{pointer} = gFreeCallbackTaskPool[idx];\n    newTask = {pointer};')
        if pointer=='insertAfter':
            replacement=replacement.replace('    insertAfter = &gCallbackTaskActiveListSentinel;\n','')+'\n    insertAfter = &gCallbackTaskActiveListSentinel;'
        options.append(('pointer-result:'+pointer,source.replace(allocation,replacement)))
    for expr in ('idx = 1','++idx','idx -= idx - 1'):
        code=source.replace('newTask->isActive = 1;',f'idx = 0; newTask->isActive = ({expr});' if expr=='++idx' else f'newTask->isActive = ({expr});')
        options.append(('reuse-index:'+expr,code))
repo=Path('/home/grant/decomp/sbk1')
ws=workspace.bootstrap(repo,name)
conn=sqlite3.connect(ROOT/'eval/results/direct-source-semantic-cohort-v1/attempts.sqlite',timeout=120)
rows=[]
for i,(label,code) in enumerate(options):
    tag=f'{name}_poolvalue_{time.time_ns()}'
    a=workspace.score(ws,repo,tag,code,conn=conn,func=name,strategy='callback-pool-value',action=label)
    rows.append(dict(label=label,tag=tag,**asdict(a)))
    (out/f'{i:02d}.c').write_text(code)
    (out/'scores.json').write_text(json.dumps(rows,indent=2))
    print(i,label,a.score,a.exact,flush=True)
    if a.score>98.511: print('IMPROVEMENT',i,flush=True)
