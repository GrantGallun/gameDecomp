"""Remove a copied traversal cursor in favor of its predecessor's next field.

The recognized loop has no calls or memory writes. Repeated source reads expose
the natural predecessor-driven traversal to IDO; compiler/replay gates still
decide whether a generated candidate is acceptable.
"""
import re
from solver import code_shapes
from solver.principle_variants import Variant


def candidates(source: str, function: str, maximum: int = 8):
    region=code_shapes._body(source,function)
    if not region or maximum<=0:
        return []
    masked,begin,end=region
    body=masked[begin:end]
    if re.search(r'\bvolatile\b',masked) or re.search(r'\b(?:goto|asm)\b',body):
        return []
    common=(r'(?P<c>\w+)\s*=\s*(?P<p>\w+)->(?P<n>\w+)\s*;\s*'
        r'for\s*\(\s*;\s*;\s*\)\s*\{\s*'
        r'if\s*\((?P=c)->(?P<f>\w+)\s*<\s*(?P<b>\w+)\)\s*break\s*;\s*'
        r'(?P=p)\s*=\s*(?P=c)\s*;\s*(?P=c)\s*=\s*(?P=c)->(?P=n)\s*;\s*'
        r'if\s*\((?P=c)\s*==\s*NULL\)\s*break\s*;\s*\}')
    pattern=re.compile(r'if\s*\((?P<head>\w+)\.(?P<slot>\w+)\s*!=\s*NULL\)\s*\{\s*'+common+r'\s*\}')
    result=[]
    for m in pattern.finditer(masked,begin,end):
        g=m.groupdict();c,p,n,f,b=(g[k] for k in ('c','p','n','f','b'))
        if n!=g['slot'] or len({c,p,b})!=3:
            continue
        # The cached cursor must be a nonescaping local and dead afterward.
        prefix=masked[begin:m.start()]
        occurrences=list(re.finditer(r'\b'+re.escape(c)+r'\b',prefix))
        declaration=re.search(r'\b[A-Za-z_]\w*\s*\*\s*'+re.escape(c)+r'\s*;',prefix)
        if len(occurrences)!=1 or not declaration or re.search(r'\b'+re.escape(c)+r'\b',masked[m.end():end]):
            continue
        assignments=list(re.finditer(r'\b'+re.escape(p)+r'\s*=(?!=)',prefix))
        if not assignments:
            continue
        reaching=prefix[assignments[-1].start():]
        pure_reaching=(re.escape(p)+r'\s*=\s*&\s*'+re.escape(g['head'])+r'\s*;\s*'
            r'(?:\w+\s*=\s*\w+\s*\[\s*\w+\s*\]\s*;\s*)*')
        if not re.fullmatch(pure_reaching,reaching):
            continue
        replacement=f'''while ({p}->{n} != NULL) {{
        if ({p}->{n}->{f} < {b}) break;
        {p} = {p}->{n};
    }}'''
        code=source[:m.start()]+replacement+source[m.end():]
        result.append(Variant('predecessor-walk:direct-next-field',code))
        if len(result)>=maximum:
            break
    return result[:maximum]
