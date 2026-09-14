"""Bounded source spellings for linked-list insertion and masked dispatch.

These are experiment candidates, requiring compilation and differential replay.
The recognizer deliberately refuses unknown list bodies.
"""
import re
from solver.principle_variants import Variant
from solver import code_shapes


def masked_parameter_casts(source: str, function: str, maximum: int = 8):
    """Pair unsigned parameter narrowing with an existing first-use mask.

    Match only full scalar parameters, never nested function-pointer arguments.
    Update matching translation-unit prototypes by parameter position, including
    unnamed parameters. A different declared type makes the candidate ineligible.
    """
    region=code_shapes._body(source,function)
    if region is None or maximum<=0:
        return []
    masked,begin,end=region
    headers=[]
    for match in re.finditer(r'\b'+re.escape(function)+r'\s*\(',masked):
        # Function calls and declarations inside another body are not headers.
        prefix=masked[:match.start()]
        if prefix.count('{') != prefix.count('}'):
            continue
        opening=match.end()-1
        closing=code_shapes._close(masked,opening,'(',')')
        if closing<0:
            continue
        after=code_shapes._space(masked,closing+1)
        if after>=len(masked) or masked[after] not in '{;':
            continue
        parts=[]
        depth=0
        start=opening+1
        for index in range(opening+1,closing):
            if masked[index] in '([': depth+=1
            elif masked[index] in ')]': depth-=1
            elif masked[index]==',' and depth==0:
                parts.append((start,index))
                start=index+1
        parts.append((start,closing))
        headers.append((after,parts))
    definition=next((parts for after,parts in headers if after==begin-1),None)
    if definition is None:
        return []
    result=[]
    for match in re.finditer(r'\b(\w+)\s*&=\s*(0[xX][fF]{4}|65535)\s*;',masked[begin:end]):
        name=match[1]
        prefix=masked[begin:begin+match.start()]
        if prefix.count('{')!=prefix.count('}') or re.search(r'\b'+re.escape(name)+r'\b',prefix):
            continue
        selected=next((i for i,(a,b) in enumerate(definition)
                       if re.fullmatch(r'\s*u32\s+'+re.escape(name)+r'\s*',masked[a:b])),None)
        if selected is None:
            continue
        edits=[]
        for _,parts in headers:
            if len(parts)!=len(definition):
                edits=[]
                break
            a,b=parts[selected]
            param=re.fullmatch(r'\s*(u32)(?:\s+[A-Za-z_]\w*)?\s*',masked[a:b])
            if param is None:
                edits=[]
                break
            edits.append((a+param.start(1),a+param.end(1),'u16'))
        if not edits:
            continue
        edits.append((begin+match.start(),begin+match.end(),f'{name} = (u16){name};'))
        changed=source
        for a,b,replacement in sorted(edits,reverse=True):
            changed=changed[:a]+replacement+changed[b:]
        result.append(Variant('masked-parameter-cast:'+name,changed))
        if len(result)>=maximum:
            break
    return result


def candidates(source: str, function: str, maximum: int = 64):
    if function != 'createCallbackTaskPreservingArgs' or maximum <= 0:
        return []
    loop = re.search(r'cur = insertAfter->next;\s*while \(cur != NULL\) \{\s*if \(cur->priority < priority\) break;\s*insertAfter = cur;\s*cur = cur->next;\s*\}', source)
    if not loop:
        return []
    bodies = [
        ('original', loop.group()),
        ('guarded-for', '''if (insertAfter->next != NULL) {
        for (cur = gCallbackTaskActiveListSentinel.next; cur != NULL; cur = cur->next) {
            if (cur->priority < priority) break;
            insertAfter = cur;
        }
    }'''),
        ('guarded-while', '''if (insertAfter->next != NULL) {
        cur = gCallbackTaskActiveListSentinel.next;
        while (cur != NULL) {
            if (cur->priority < priority) break;
            insertAfter = cur;
            cur = cur->next;
        }
    }'''),
        ('head-for', '''cur = gCallbackTaskActiveListSentinel.next;
    for (; cur != NULL && cur->priority >= priority; cur = cur->next) {
        insertAfter = cur;
    }'''),
    ]
    # A narrow parameter already applies the conversion. An explicit bitwise
    # assignment gives IDO a different parameter lifetime than its cast form.
    # Change every matching prototype together; replay checks ABI edge cases.
    result=masked_parameter_casts(source,function,maximum)
    if len(result)>=maximum:
        return result[:maximum]
    seen={source}
    for loop_label, body in bodies:
        loop_source=source[:loop.start()]+body+source[loop.end():]
        for type_label in ('u32','u16','s32'):
            typed=re.sub(r'\bu32 type\b', type_label+' type',loop_source)
            for order in ('original','cur-first','insert-first','idx-u32','idx-s32'):
                code=typed
                if order=='cur-first':
                    code=code.replace('CallbackTask *newTask;\n    CallbackTask *insertAfter;\n    CallbackTask *cur;', 'CallbackTask *cur;\n    CallbackTask *insertAfter;\n    CallbackTask *newTask;')
                elif order=='insert-first':
                    code=code.replace('CallbackTask *newTask;\n    CallbackTask *insertAfter;', 'CallbackTask *insertAfter;\n    CallbackTask *newTask;')
                elif order.startswith('idx-'):
                    code=code.replace('u16 idx;',order[4:]+' idx;')
                if code not in seen:
                    seen.add(code)
                    result.append(Variant(f'callback:{loop_label}:{type_label}:{order}',code))
                    if len(result)>=maximum:
                        return result
    return result
