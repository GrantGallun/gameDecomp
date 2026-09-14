import json,sys,time,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from solver import workspace,callback_selector_alternatives
from solver.principle_variants import Variant
source=(ROOT/'eval/results/callback-inverse-final/createCallbackTaskPreservingArgs.c').read_text()
out=ROOT/('eval/results/callback-allocation-selector-v6' if '--stage-pair' in sys.argv else 'eval/results/callback-allocation-selector-v5' if '--prologue' in sys.argv else 'eval/results/callback-allocation-selector-v4' if '--scratch' in sys.argv else 'eval/results/callback-allocation-selector-v3' if '--final-two' in sys.argv else 'eval/results/callback-allocation-selector-v2' if '--demote' in sys.argv else 'eval/results/callback-allocation-selector-v1');out.mkdir(exist_ok=True)
repo=Path('/home/grant/decomp/sbk1');ws=repo/'nonmatchings/createCallbackTaskPreservingArgs'
conn=sqlite3.connect(ROOT/'eval/results/direct-source-semantic-cohort-v1/attempts.sqlite',timeout=120)
options=[Variant('baseline',source)]+callback_selector_alternatives.candidates(source,'createCallbackTaskPreservingArgs')
if '--demote' in sys.argv:
    import re
    options=[]
    for label,expr in [
        ('diagnostic-volatile-u16','*(volatile u16 *)&gFreeCallbackTaskCount'),
        ('diagnostic-volatile-s16','(u16)*(volatile s16 *)&gFreeCallbackTaskCount'),
        ('diagnostic-volatile-array','((volatile u16 *)&gFreeCallbackTaskCount)[0]'),
        ('plain-array','((u16 *)&gFreeCallbackTaskCount)[0]'),
        ('byte-address','*(u16 *)(void *)&gFreeCallbackTaskCount'),
        ('signed-load','(u16)*(s16 *)&gFreeCallbackTaskCount'),
        ('pointer-through-char','*(u16 *)((char *)&gFreeCallbackTaskCount)'),
    ]:
        options.append(Variant(label,source.replace('idx = gFreeCallbackTaskCount;',f'idx = {expr};')))
    for typ in ('u16','s16','const u16'):
        code=source.replace('u16 idx;',f'u16 idx;\n    {typ} *countPointer;').replace('if (gFreeCallbackTaskCount == 0)',f'countPointer = ({typ} *)&gFreeCallbackTaskCount;\n    if (*countPointer == 0)').replace('gFreeCallbackTaskCount--;','--*countPointer;').replace('idx = gFreeCallbackTaskCount;','idx = (u16)*countPointer;')
        if typ=='const u16':code=code.replace('--*countPointer;','gFreeCallbackTaskCount--;')
        options.append(Variant('count-pointer:'+typ,code))
    for typ in ('array','struct','union'):
        if typ=='array':
            code=source.replace('extern u16 gFreeCallbackTaskCount;','extern u16 gFreeCallbackTaskCount[1];')
            pos=code.index('/* Implementation */');code=code[:pos]+re.sub(r'\bgFreeCallbackTaskCount\b','gFreeCallbackTaskCount[0]',code[pos:])
        else:
            decl=('struct { u16 value; }' if typ=='struct' else 'union { u16 value; u16 words[1]; }')
            code=source.replace('extern u16 gFreeCallbackTaskCount;',f'extern {decl} gFreeCallbackTaskCount;')
            pos=code.index('/* Implementation */');code=code[:pos]+re.sub(r'\bgFreeCallbackTaskCount\b','gFreeCallbackTaskCount.value',code[pos:])
            if typ=='union':code=code.replace('idx = gFreeCallbackTaskCount.value;','idx = gFreeCallbackTaskCount.words[0];')
        options.append(Variant('counter-layout:'+typ,code))
if '--final-two' in sys.argv:
    code=(ROOT/'eval/results/callback-inverse-joint-v2/01.c').read_text()
    old='    insertAfter = &gCallbackTaskActiveListSentinel;\n    newTask = gFreeCallbackTaskPool[gFreeCallbackTaskCount];'
    reversed_code=code.replace(old,'    newTask = gFreeCallbackTaskPool[gFreeCallbackTaskCount];\n    insertAfter = &gCallbackTaskActiveListSentinel;')
    base_first=code.replace('    insertAfter = &gCallbackTaskActiveListSentinel;\n','').replace('    if (gFreeCallbackTaskCount == 0)','    insertAfter = &gCallbackTaskActiveListSentinel;\n    if (gFreeCallbackTaskCount == 0)')
    options=[Variant('inline-index-and-lookup-first',reversed_code),Variant('inline-index-and-base-first',base_first)]
if '--scratch' in sys.argv:
    code=(ROOT/'eval/results/callback-allocation-selector-v3/00.c').read_text()
    variants=callback_selector_alternatives.candidates(code,'createCallbackTaskPreservingArgs')
    options=[v for v in variants if v.label in ('selector-relation:switch-assignment:(u8)(type &= 0xffff)','selector-relation:preserved-local:s32:before','selector-relation:full-type-carrier:s32')]
    options += [Variant('chained-u32-u16',code.replace('type = (u16)type;','type = (u16)(u32)(u16)type;')),
        Variant('chained-shift',code.replace('type = (u16)type;','type = (u32)type << 16 >> 16;')),
        Variant('type-bitand',code.replace('type = (u16)type;','type &= 0xffff;')),
        Variant('u32-parameter-mask',code.replace('u16 type, s32 priority','u32 type, s32 priority')),
        Variant('s32-parameter-mask',code.replace('u16 type, s32 priority','s32 type, s32 priority'))]
if '--prologue' in sys.argv:
    code=(ROOT/'eval/results/callback-allocation-backend-v2/00.c').read_text()
    old='type = (u16)(s16)type;\n    t8 = type;'
    options=[Variant(label,code.replace(old,text)) for label,text in [
        ('double-normalize','type = (u16)(s16)type;\n    type = (u16)type;\n    t8 = type;'),
        ('signed-selector-only','type = (u16)type;\n    t8 = (u8)(s16)type;'),
        ('extra-selector-cast','type = (u16)(s16)type;\n    t8 = (u8)(u16)type;'),
        ('s32-s16-chain','type = (u16)(s32)(s16)type;\n    t8 = type;'),
        ('u32-s16-chain','type = (u16)(s16)(u32)type;\n    t8 = type;'),
        ('signed-selector-before','t8 = (u8)(s16)type;\n    type = (u16)type;')]]
    options += [Variant('s16-local',code.replace('u8 t8;','u8 t8;\n    s16 signedType;').replace(old,'signedType = type;\n    type = (u16)signedType;\n    t8 = type;')),
        Variant('u16-saved-local',code.replace('u8 t8;','u8 t8;\n    u16 savedType;').replace(old,'savedType = (u16)(s16)type;\n    t8 = savedType;').replace('newTask->type = type;','newTask->type = savedType;'))]
if '--stage-pair' in sys.argv:
    code=(ROOT/'eval/results/callback-allocation-backend-v2/00.c').read_text()
    old='type = (u16)(s16)type;\n    t8 = type;'
    options=[Variant('defer-parameter-store:'+typ,code.replace('u8 t8;',f'u8 t8;\n    {typ} normalizedType;').replace(old,'normalizedType = (u16)(s16)type;\n    t8 = normalizedType;\n    type = normalizedType;')) for typ in ('u16','u32')]
rows=[]
for i,v in enumerate(options):
    tag=f'createCallbackTaskPreservingArgs_selector_relation_{time.time_ns()}'
    a=workspace.score(ws,repo,tag,v.source,conn,'createCallbackTaskPreservingArgs',strategy='callback-selector-relation',action=v.label)
    (out/f'{i:02d}.c').write_text(v.source);(out/f'{i:02d}.diff').write_text(a.diff)
    row=dict(index=i,label=v.label,score=a.score,compiled=a.compiled,exact=a.exact,attempt=a.receipt_id,artifact=tag)
    rows.append(row);(out/'summary.json').write_text(json.dumps(rows,indent=2));print(row,flush=True)
