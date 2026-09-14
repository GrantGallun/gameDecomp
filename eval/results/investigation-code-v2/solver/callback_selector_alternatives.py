"""Experimental relationships between preserved u16 type and u8 dispatch.

These are bounded oracle-gated probes, not assumed byte improvements.
"""
from solver import code_shapes
from solver.principle_variants import Variant


def candidates(source,function,maximum=40):
    region=code_shapes._body(source,function)
    if not region or maximum<=0:return []
    masked,begin,end=region
    body=source[begin:end]
    if not all(x in masked[begin:end] for x in ('u8 t8;','type = (u16)type;','t8 = type;','switch (t8)','newTask->type = type;')):
        return []
    if any(x in masked for x in ('volatile','savedType','typeParts')):return []
    result=[];seen={source}
    def emit(label,b):
        code=source[:begin]+b+source[end:]
        if code not in seen:
            seen.add(code);result.append(Variant('selector-relation:'+label,code))
    for typ in ('u16','u32','s32'):
        for order in ('before','after','via-selector'):
            b=body.replace('u8 t8;',f'u8 t8;\n    {typ} savedType;')
            if order=='before':b=b.replace('type = (u16)type;\n    t8 = type;','savedType = (u16)type;\n    t8 = savedType;')
            elif order=='after':b=b.replace('type = (u16)type;\n    t8 = type;','t8 = type;\n    savedType = (u16)type;')
            else:b=b.replace('type = (u16)type;\n    t8 = type;','savedType = (u16)type;\n    t8 = savedType;\n    type = (u16)savedType;')
            b=b.replace('newTask->type = type;','newTask->type = savedType;')
            emit('preserved-local:'+typ+':'+order,b)
    for expression in ('(u8)(type = (u16)type)','(u8)(t8 = type)','(u8)(type &= 0xffff)','((u16)type) & 0xff'):
        b=body.replace('type = (u16)type;','').replace('t8 = type;','').replace('switch (t8)',f'switch ({expression})')
        emit('switch-assignment:'+expression,b)
    for typ in ('u16','u32','s32'):
        b=body.replace('u8 t8;',f'{typ} t8;').replace('type = (u16)type;\n    t8 = type;','t8 = (u16)type;\n    type = (u16)t8;').replace('switch (t8)','switch ((u8)t8)').replace('newTask->type = type;','newTask->type = t8;')
        emit('full-type-carrier:'+typ,b)
    for expression in ('type & 0xff00','type - t8','(u16)type ^ t8'):
        b=body.replace('u8 t8;','u8 t8;\n    u16 savedType;').replace('t8 = type;',f't8 = type;\n    savedType = {expression};').replace('newTask->type = type;','newTask->type = savedType | t8;')
        emit('split-high:'+expression,b)
    for style in ('positive','negated-positive','decrement-store','assignment-check','subassign'):
        b=body
        for n in range(7):
            count=f'gFreeCallbackTaskType{n}Count'
            old=f'if ({count} == 0) return NULL;\n        {count}--;\n        break;'
            if style=='positive':new=f'if ({count} != 0) {{ {count}--; break; }}\n        return NULL;'
            elif style=='negated-positive':new=f'if (!({count} > 0)) return NULL;\n        {count}--;\n        break;'
            elif style=='decrement-store':new=f'if ({count} == 0) return NULL;\n        {count} = (u16)({count} - 1);\n        break;'
            elif style=='assignment-check':new=f'if ((t8 = ({count} != 0)) == 0) return NULL;\n        {count}--;\n        break;'
            else:new=f'if ({count} == 0) return NULL;\n        {count} -= 1;\n        break;'
            b=b.replace(old,new)
        emit('counter:'+style,b)
    return result[:maximum]
