"""Conservative source hypotheses for guarded pointer traversal.

Only a recognized local-pointer walk with a single comparison exit is changed;
the compiler/semantic oracle remains the acceptance gate.
"""
import re
from solver import code_shapes, principle_variants


def candidates(source, function, maximum=24):
    region=code_shapes._body(source,function)
    if not region or maximum<=0:
        return []
    masked,start,end=region
    if re.search(r'\b(?:volatile|goto|asm)\b',masked[start:end]):
        return []
    pattern=re.compile(r'(?P<cur>\w+)\s*=\s*(?P<prev>\w+)->(?P<next>\w+)\s*;\s*while\s*\((?P=cur)\s*!=\s*NULL\)\s*\{\s*if\s*\((?P=cur)->(?P<field>\w+)\s*<\s*(?P<bound>\w+)\)\s*break;\s*(?P=prev)\s*=\s*(?P=cur)\s*;\s*(?P=cur)\s*=\s*(?P=cur)->(?P=next)\s*;\s*\}')
    result=[]
    for m in pattern.finditer(masked,start,end):
        g=m.groupdict(); c,p,n,f,b=(g[k] for k in ('cur','prev','next','field','bound'))
        if len({c,p,b})!=3:
            continue
        if re.search(r'\b'+re.escape(c)+r'\b',masked[m.end():end]):
            continue
        if not re.search(r'\b(?:u8|s8|u16|s16|u32|s32|int|unsigned\s+int)\s+'+re.escape(f)+r'\s*;',masked[:start]):
            continue
        # Values must be local pointers/scalars, not member or array lvalues.
        before=masked[:m.start()].rstrip()
        if not before.endswith((';','{','}')):
            continue
        init=f'{c} = {p}->{n};'
        step=f'{p} = {c}; {c} = {c}->{n};'
        comparison=f'{c}->{f} < {b}'
        forms=[
            ('for-step',f'for ({c} = {p}->{n}; {c} != NULL; {c} = {c}->{n}) {{ if ({comparison}) break; {p} = {c}; }}'),
            ('combined-condition',f'{init}\n    while ({c} != NULL && {c}->{f} >= {b}) {{ {step} }}'),
            ('guard-reload',f'if ({p}->{n} != NULL) {{ {init} while ({c} != NULL) {{ if ({comparison}) break; {step} }} }}'),
            ('guard-for-reload',f'if ({p}->{n} != NULL) {{ for ({c} = {p}->{n}; {c} != NULL; {c} = {c}->{n}) {{ if ({comparison}) break; {p} = {c}; }} }}'),
            ('guard-top-break',f'if ({p}->{n} != NULL) {{ {init} for (;;) {{ if ({comparison}) break; {step} if ({c} == NULL) break; }} }}'),
            ('guard-combined',f'if ({p}->{n} != NULL) {{ {init} while ({c} != NULL && {c}->{f} >= {b}) {{ {step} }} }}'),
            ('guard-invert',f'{init} if ({c} != NULL) {{ for (;;) {{ if ({c}->{f} >= {b}) {{ {step} if ({c} == NULL) break; }} else break; }} }}'),
        ]
        # A separately written sentinel expression exposes the original guard
        # and traversal entry without relying on a cached pointer spelling.
        sentinel=re.search(r'\b'+re.escape(p)+r'\s*=\s*&\s*(\w+)\s*;',masked[start:m.start()])
        if sentinel and re.search(r'\b'+re.escape(p)+r'\s*=',masked[start+sentinel.end():m.start()]):
            sentinel=None
        if sentinel:
            s=sentinel.group(1)
            forms += [('sentinel-'+label,body.replace(f'{p}->{n}',f'{s}.{n}')) for label,body in forms if label.startswith('guard')]
        for label,body in forms:
            code=source[:m.start()]+body+source[m.end():]
            if code!=source and all(v.source!=code for v in result):
                result.append(principle_variants.Variant('pointer-walk:'+label,code))
    return result[:maximum]
