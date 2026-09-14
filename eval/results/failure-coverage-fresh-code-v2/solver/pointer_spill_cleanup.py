"""Remove dead pointer-only copies, never side-effecting assignments or reads."""
import hashlib
import re
from solver import project_headers, repair_context


def propose(source, function):
    report={'source':source,'changes':[],
        'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'authority':'closed local pointer-copy cleanup; compiler/oracle still adjudicate codegen'}
    try:
        definition,end=repair_context.definition(source,function)
    except ValueError:
        return report
    mask=project_headers._mask_noncode(source)
    body=mask[definition.end():end-1]
    declarations=list(re.finditer(r'(?m)^[ \t]*((?:struct\s+)?\w+)\s*\*\s*(\w+)\s*;',body))
    edits=[]
    for declaration in declarations:
        name=declaration[2]
        if sum(d[2]==name for d in declarations)!=1:
            continue
        uses=[m for m in re.finditer(r'\b'+name+r'\b',body)
            if not declaration.start()<=m.start()<declaration.end()]
        assignments=list(re.finditer(r'(?m)^[ \t]*('+name+r')\s*=\s*(\w+)\s*;',body))
        if not uses or len(uses)!=len(assignments) or any(not any(a.start(1)==u.start() for a in assignments) for u in uses):
            continue
        # RHS may only read another uniquely declared, nonvolatile local
        # pointer. Calls, dereferences, casts, globals, increments and chains
        # are deliberately outside this side-effect-free grammar.
        if any(a[2]==name or sum(d[2]==a[2] for d in declarations)!=1 for a in assignments):
            continue
        edits.append((declaration.start(),declaration.end(),''))
        edits.extend((a.start(),a.end(),'') for a in assignments)
        report['changes'].append({'local':name,'assignment_count':len(assignments),
            'rhs_locals':sorted({a[2] for a in assignments})})
    for start,stop,replacement in sorted(edits,reverse=True):
        source=source[:definition.end()+start]+replacement+source[definition.end()+stop:]
    report.update(source=source,candidate_sha256=hashlib.sha256(source.encode()).hexdigest())
    return report
