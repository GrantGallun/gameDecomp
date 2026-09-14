"""Bounded experiments for independently loaded linked-list heads.

Candidates include explicit access qualifiers. They must pass the production
compiler/frontend and semantic replay before selection; these are not static
equivalence certificates for concurrent or memory-mapped environments.
"""
import re
from solver.principle_variants import Variant
from solver import code_shapes


def candidates(source: str, function: str, maximum: int = 60):
    region=code_shapes._body(source,function)
    if region is None or maximum<=0:
        return []
    masked,begin,end=region
    pattern=r'cur = insertAfter->next;\s*while \(cur != NULL\) \{\s*if \(cur->priority < priority\) break;\s*insertAfter = cur;\s*cur = cur->next;\s*\}'
    match=re.search(pattern,masked[begin:end])
    if not match:
        return []
    start=begin+match.start();stop=begin+match.end()
    assignments=list(re.finditer(r'\binsertAfter\s*=(?!=)',masked[begin:start]))
    if not assignments:
        return []
    # The extra guard must read the same head as the original loop. Decline
    # unknown reaching definitions or intervening effects on the base pointer.
    reaching=masked[begin+assignments[-1].start():start]
    if not re.fullmatch(r'insertAfter\s*=\s*&gCallbackTaskActiveListSentinel\s*;\s*'
                        r'newTask\s*=\s*gFreeCallbackTaskPool\s*\[\s*idx\s*\]\s*;\s*',reaching):
        return []
    heads=[
        ('member','gCallbackTaskActiveListSentinel.next'),
        ('volatile-struct','((volatile CallbackTask *)&gCallbackTaskActiveListSentinel)->next'),
        ('volatile-slot','*(CallbackTask * volatile *)&gCallbackTaskActiveListSentinel.next'),
        ('integer-slot','(CallbackTask *)*(u32 *)&gCallbackTaskActiveListSentinel.next'),
        ('byte-offset','*(CallbackTask **)((u8 *)&gCallbackTaskActiveListSentinel + 4)'),
        ('via-insert','insertAfter->next'),
    ]
    result=[];seen={source}
    # Prioritize changing the reloaded expression, then changing the guard.
    combinations=[(heads[0],heads[-1])]
    combinations += [(heads[0],h) for h in heads[:-1]]
    combinations += [(g,h) for g in heads[1:] for h in heads]
    for (guard_label,guard),(head_label,head) in combinations:
        for form in ('bottom','while'):
            if form=='bottom':
                body=f'''if ({guard} != NULL) {{
        cur = {head};
        for (;;) {{
            if (cur->priority < priority) break;
            insertAfter = cur;
            cur = cur->next;
            if (cur == NULL) break;
        }}
    }}'''
            else:
                body=f'''if ({guard} != NULL) {{
        cur = {head};
        while (cur != NULL) {{
            if (cur->priority < priority) break;
            insertAfter = cur;
            cur = cur->next;
        }}
    }}'''
            code=source[:start]+body+source[stop:]
            if code not in seen:
                seen.add(code)
                result.append(Variant(f'callback-reload:{guard_label}:{head_label}:{form}',code))
                if len(result)>=maximum:return result
    return result
