"""Bounded joint source-shape experiment for callback allocation and traversal.

This is a candidate synthesizer, not an equivalence certificate. Each emitted
form must pass the production compiler and complete differential panel.
"""
import itertools
from solver import code_shapes
from solver.principle_variants import Variant


def candidates(source, function, maximum=96):
    if function != 'createCallbackTaskPreservingArgs' or maximum<=0:
        return []
    region=code_shapes._body(source,function)
    if not region:
        return []
    masked,begin,end=region
    body=source[begin:end]
    allocation='''gFreeCallbackTaskCount--;
    idx = gFreeCallbackTaskCount;
    insertAfter = &gCallbackTaskActiveListSentinel;
    newTask = gFreeCallbackTaskPool[idx];'''
    guard='''if (gCallbackTaskActiveListSentinel.next != NULL) {
        cur = insertAfter->next;'''
    if allocation not in body or guard not in body or 'u16 idx;' not in body:
        return []
    if any(word in masked[begin:end].split() for word in ('volatile','goto','asm')):
        return []
    allocs=[('original',allocation),
      ('inline',allocation.replace('    idx = gFreeCallbackTaskCount;\n','').replace('Pool[idx]','Pool[gFreeCallbackTaskCount]')),
      ('index-first',allocation.replace('gFreeCallbackTaskCount--;\n    idx = gFreeCallbackTaskCount;', 'idx = gFreeCallbackTaskCount - 1;\n    gFreeCallbackTaskCount = idx;')),
      ('lookup-first',allocation.replace('    insertAfter = &gCallbackTaskActiveListSentinel;\n','')+'\n    insertAfter = &gCallbackTaskActiveListSentinel;'),
      ('base-first','insertAfter = &gCallbackTaskActiveListSentinel;\n    '+allocation.replace('    insertAfter = &gCallbackTaskActiveListSentinel;\n','')),
      ('pointer-lookup',allocation.replace('gFreeCallbackTaskPool[idx]','*(gFreeCallbackTaskPool + idx)')),
      ('local-count',allocation.replace('gFreeCallbackTaskCount--;\n    idx = gFreeCallbackTaskCount;', 'idx = gFreeCallbackTaskCount;\n    --idx;\n    gFreeCallbackTaskCount = idx;')),
      ('subtract-lookup',allocation.replace('gFreeCallbackTaskCount--;\n    idx = gFreeCallbackTaskCount;','idx = gFreeCallbackTaskCount;\n    gFreeCallbackTaskCount = idx - 1;').replace('Pool[idx]','Pool[idx - 1]'))]
    heads=[('direct',guard),
      ('new-base',guard.replace('cur = insertAfter->next;','{ CallbackTask *head; head = &gCallbackTaskActiveListSentinel; cur = head->next; }')),
      ('address-next',guard.replace('cur = insertAfter->next;','cur = (&gCallbackTaskActiveListSentinel)->next;')),
      ('base-reassign',guard.replace('cur = insertAfter->next;','insertAfter = &gCallbackTaskActiveListSentinel; cur = insertAfter->next;'))]
    styles=('original','late-allocation','late-active')
    result=[]
    seen={source}
    # Round-robin combinations include neutral and lower-scoring one-step forms
    # without requiring them to win the ordinary greedy frontier first.
    for ai,hi,si in itertools.product(range(8),range(4),range(3)):
        alabel,atext=allocs[ai]
        hlabel,htext=heads[hi]
        changed=body.replace(allocation,atext).replace(guard,htext)
        style=styles[si]
        if style=='late-allocation':
            lines=changed.splitlines()
            lookup=next((line for line in lines if 'newTask = ' in line),None)
            if lookup is None: continue
            changed=changed.replace(lookup+'\n','',1).replace('    newTask->prev = insertAfter;',lookup+'\n    newTask->prev = insertAfter;',1)
        elif style=='late-active':
            changed=changed.replace('    newTask->isActive = 1;\n','').replace('    return newTask;','    newTask->isActive = 1;\n    return newTask;')
        candidate=source[:begin]+changed+source[end:]
        if candidate not in seen:
            seen.add(candidate)
            result.append(Variant(f'callback-joint:{alabel}:{hlabel}:{style}',candidate))
        if len(result)>=maximum: break
    return result
