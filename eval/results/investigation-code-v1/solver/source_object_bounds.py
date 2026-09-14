"""Conservative source-local byte-view extent obligations, never a proof of UB reachability."""
import hashlib
import re
from solver import project_headers,repair_context


def obligations(source,function):
    try:
        definition,end=repair_context.definition(source,function)
    except ValueError as exc:
        return [{'kind':'source-object-analysis-unavailable','reason':str(exc),
            'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'authority':'unsupported source syntax; no extent claim'}]
    masked=project_headers._mask_noncode(source)
    body=masked[definition.end():end-1]
    widths={'s8':1,'u8':1,'s16':2,'u16':2,'s32':4,'u32':4,'f32':4,'s64':8,'u64':8,'f64':8}
    declarations={}
    for m in re.finditer(r'(?m)^[ \t]*(s8|u8|s16|u16|s32|u32|f32|s64|u64|f64)\s+(\w+)\s*;',body):
        declarations.setdefault(m[2],[]).append((m[1],m.start()))
    # Exact lowerer's typed byte-view dialect only; do not infer arbitrary casts,
    # header aggregate sizes, pointer pointees or variable indexes.
    pattern=(r'\(\*\((s8|u8|s16|u16|s32|u32|f32|s64|u64|f64)\s*\*\)'
        r'\(\(u8\s*\*\)\(&\s*(\w+)\)\s*\+\s*(-?(?:0x[0-9a-fA-F]+|[0-9]+))\)\)')
    rows={}
    for m in re.finditer(pattern,body):
        typ,local,literal=m.groups()
        decls=declarations.get(local,[])
        if len(decls)!=1: continue
        try: offset=int(literal,0)
        except ValueError: continue
        declared=widths[decls[0][0]]
        accessed=widths[typ]
        if 0<=offset and offset+accessed<=declared: continue
        row=rows.setdefault(local,{'local':local,'declared_type':decls[0][0],
            'declared_bytes':declared,'minimum_accessed_offset':offset,
            'required_end_offset':offset+accessed,'accesses':[]})
        row['minimum_accessed_offset']=min(row['minimum_accessed_offset'],offset)
        row['required_end_offset']=max(row['required_end_offset'],offset+accessed)
        row['accesses'].append({'offset':offset,'width':accessed,
            'line':source[:definition.end()+m.start()].count('\n')+1,'expression':m[0]})
    identity=hashlib.sha256(source.encode()).hexdigest()
    return [{**row,'kind':'scalar-byte-view-extent-conflict','source_sha256':identity,
        'authority':'explicit scalar declaration and literal typed byte access; not callee behavior or path proof',
        'next_action':'Reconstruct the containing object and overlapping locals from binary/header evidence; do not merely move the stack address.'}
        for row in rows.values()]
