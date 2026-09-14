"""Bounded callback local lifetime and dispatch-storage experiments."""
import re
from solver import code_shapes
from solver.principle_variants import Variant


def candidates(source, function, maximum=60):
    if function != 'createCallbackTaskPreservingArgs':
        return []
    region=code_shapes._body(source,function)
    if not region or maximum<=0:
        return []
    masked,begin,end=region
    body=source[begin:end]
    if re.search(r'\b(?:volatile|asm|goto)\b',masked[begin:end]):
        return []
    if not all(s in masked[begin:end] for s in ('u16 idx;', 'u8 t8;', 'switch (t8)', 'idx = gFreeCallbackTaskCount;')):
        return []
    result=[]
    seen={source}
    def emit(label,changed):
        full=source[:begin]+changed+source[end:]
        if full not in seen:
            seen.add(full)
            result.append(Variant('dispatch:'+label,full))
    for flag in range(1,8):
        b=body
        for bit,name in enumerate(('newTask','insertAfter','cur')):
            if flag & (1<<bit): b=b.replace(f'CallbackTask *{name};',f'register CallbackTask *{name};')
        emit(f'register-pointers:{flag}',b)
    for typ in ('s32','u32','s16','register u16','register u32'):
        emit('idx-type:'+typ,body.replace('u16 idx;',typ+' idx;'))
    for typ in ('u32','s32','u16','register u8','register s32'):
        b=body.replace('u8 t8;',typ+' t8;').replace('t8 = type;','t8 = (u8)type;')
        emit('selector-type:'+typ,b)
    for expression in ('(u8)type','type & 255','(s32)(u8)type','(u32)(u8)type'):
        emit('inline-selector:'+expression,body.replace('u8 t8;','').replace('t8 = type;','').replace('switch (t8)',f'switch ({expression})'))
    for expr in ('--gFreeCallbackTaskCount','(gFreeCallbackTaskCount -= 1)','(gFreeCallbackTaskCount = gFreeCallbackTaskCount - 1)'):
        emit('allocate:'+expr,body.replace('gFreeCallbackTaskCount--;\n    idx = gFreeCallbackTaskCount;',f'idx = {expr};'))
    emit('inline-index',body.replace('u16 idx;','').replace('    idx = gFreeCallbackTaskCount;\n','').replace('gFreeCallbackTaskPool[idx]','gFreeCallbackTaskPool[gFreeCallbackTaskCount]'))
    for typ in ('u16','u32','s32'):
        for assignment in ('before','inside'):
            b=body.replace('u16 idx;',f'u16 idx;\n    {typ} count;')
            for n in range(7):
                name=f'gFreeCallbackTaskType{n}Count'
                old=f'if ({name} == 0) return NULL;\n        {name}--;'
                new=(f'count = {name};\n        if (count == 0) return NULL;\n        {name} = count - 1;' if assignment=='before' else
                     f'if ((count = {name}) == 0) return NULL;\n        {name} = count - 1;')
                b=b.replace(old,new)
            emit('shared-count:'+typ+':'+assignment,b)
    # End the pool index lifetime before allocating pointer locals.
    for name in ('newTask','cur','insertAfter'):
        declaration=f'CallbackTask *{name};'
        marker=('newTask = gFreeCallbackTaskPool[idx];' if name=='newTask' else 'cur = insertAfter->next;' if name=='cur' else 'insertAfter = &gCallbackTaskActiveListSentinel;')
        if declaration in body and marker in body:
            b=body.replace(declaration,'')
            p=b.index(marker)
            b=b[:p]+'{ '+declaration+' '+b[p:]+'\n}'
            emit('scope:'+name,b)
    return result[:maximum]
