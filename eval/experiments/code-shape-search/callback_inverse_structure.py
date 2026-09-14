"""Focused higher-level callback reconstruction hypotheses; oracle-gated."""
import json,sys,sqlite3,time,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from solver import workspace,code_shapes,principle_variants
source=(ROOT/'eval/results/callback-finish-final/createCallbackTaskPreservingArgs.c').read_text()
out=ROOT/'eval/results/callback-inverse-structure-v1';out.mkdir(exist_ok=True)
repo=Path('/home/grant/decomp/sbk1');ws=repo/'nonmatchings/createCallbackTaskPreservingArgs'
conn=sqlite3.connect(ROOT/'eval/results/direct-source-semantic-cohort-v1/attempts.sqlite',timeout=120)
start=source.index('    if (gCallbackTaskActiveListSentinel.next != NULL)')
end=source.index('    /* Insert newTask',start)
loops=[
    ('predecessor-while','''while (insertAfter->next != NULL) {
        cur = insertAfter->next;
        if (cur->priority < priority) break;
        insertAfter = cur;
    }'''),
    ('predecessor-for','''for (; insertAfter->next != NULL; insertAfter = cur) {
        cur = insertAfter->next;
        if (cur->priority < priority) break;
    }'''),
    ('predecessor-shortcircuit','''while (insertAfter->next != NULL && insertAfter->next->priority >= priority) {
        insertAfter = insertAfter->next;
    }'''),
    ('predecessor-assignment','''while ((cur = insertAfter->next) != NULL) {
        if (cur->priority < priority) break;
        insertAfter = cur;
    }'''),
    ('predecessor-guarded','''if (insertAfter->next != NULL) {
        for (;;) {
            cur = insertAfter->next;
            if (cur->priority < priority) break;
            insertAfter = cur;
            if (insertAfter->next == NULL) break;
        }
    }'''),
    ('predecessor-global-guarded','''if (gCallbackTaskActiveListSentinel.next != NULL) {
        for (;;) {
            cur = insertAfter->next;
            if (cur->priority < priority) break;
            insertAfter = cur;
            if (insertAfter->next == NULL) break;
        }
    }'''),
    ('predecessor-next-priority','''while (insertAfter->next != NULL) {
        if (insertAfter->next->priority < priority) break;
        insertAfter = insertAfter->next;
    }'''),
    ('predecessor-guarded-next-priority','''if (gCallbackTaskActiveListSentinel.next != NULL) {
        for (;;) {
            if (insertAfter->next->priority < priority) break;
            insertAfter = insertAfter->next;
            if (insertAfter->next == NULL) break;
        }
    }'''),
]
variants=[('baseline',source)]+[(label,source[:start]+'    '+body+'\n\n'+source[end:]) for label,body in loops]
initial=list(variants)
for label,code in initial:
    line='    newTask = gFreeCallbackTaskPool[idx];\n'
    marker='    /* Insert newTask after insertAfter */'
    moved=code.replace(line,'').replace(marker,line+'\n'+marker)
    variants.append((label+'-allocate-after-traversal',moved))
    a=code.index('    if (gFreeCallbackTaskCount == 0) return NULL;')
    b=code.rfind('    return newTask;')
    wrapped=code[:a]+'    if (gFreeCallbackTaskCount != 0) {\n'+code[a+len('    if (gFreeCallbackTaskCount == 0) return NULL;'):b]+'    return newTask;\n    }\n    return NULL;\n'+code[b+len('    return newTask;\n'):]
    variants.append((label+'-positive-allocation-block',wrapped))
rows=[]
for i,(label,code) in enumerate(variants):
    tag=f'createCallbackTaskPreservingArgs_structural_{time.time_ns()}'
    a=workspace.score(ws,repo,tag,code,conn,'createCallbackTaskPreservingArgs',strategy='callback-structure',action=label)
    (out/f'{i:02d}.c').write_text(code);(out/f'{i:02d}.diff').write_text(a.diff)
    row=dict(index=i,label=label,score=a.score,compiled=a.compiled,exact=a.exact,attempt=a.receipt_id,artifact=tag)
    rows.append(row);(out/'summary.json').write_text(json.dumps(rows,indent=2));print(row,flush=True)
