"""Measured partial-word store candidates for already typed parameter roots."""
import hashlib
import re
from solver import dataflow, project_headers, repair_context


def propose(source, function, assembly, measured, diagnostics):
    digest=hashlib.sha256(source.encode()).hexdigest()
    report={'source':source,'source_sha256':digest,'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
            'changes':[],'declines':[],'measurement':measured,
            'authority':'header/binary-constrained word-view candidate; not semantic proof'}
    if measured.get('source_sha256')!=digest:
        raise ValueError('partial-word measurement source mismatch')
    definition,end=repair_context.definition(source,function)
    first=re.fullmatch(r'\s*(\w+)\s*\*\s*(\w+)\s*',definition[2].split(',')[0])
    if not first:
        return report
    ctype,root=first.groups()
    body=project_headers._mask_noncode(source)[definition.end():end-1]
    if re.search(r'\b'+root+r'\s*(?:=(?!=)|\+=|-=|\+\+|--)|&\s*\b'+root+r'\b(?!\s*->)',body):
        return report
    missing=set(re.findall(r"no member named '(unk[0-9a-fA-F]+)'",diagnostics))
    flow=dataflow.analyse(assembly)
    edits=[]
    for member in sorted(missing):
        offset=int(member[3:],16)
        fields=[f for f in measured.get('layouts',{}).get(ctype,[]) if f.get('width')==8
                and f.get('spelling') in {'u64','s64'} and offset in {f['offset'],f['offset']+4}
                and offset+4<=f.get('owner_size',0)]
        witnesses=[i for i,a in flow.accesses.items() if a.opcode=='sw'
                   and a.address==dataflow.Value.address('param0',offset)]
        uses=list(re.finditer(r'\b'+root+r'\s*->\s*'+member+r'\b',body))
        assignments=list(re.finditer(r'(?m)^[ \t]*'+root+r'\s*->\s*'+member+r'\s*=\s*([^;{}]+);',body))
        if len(fields)!=1 or len(witnesses)!=1 or len(uses)!=1 or len(assignments)!=1:
            report['declines'].append({'member':member,'reason':'requires unique measured subfield and source/binary store'})
            continue
        m=assignments[0]
        if not m.start()<=uses[0].start()<m.end():
            continue
        rhs=source[definition.end()+m.start(1):definition.end()+m.end(1)]
        replacement=f'    (*(u32 *)((u8 *){root} + {offset:#x})) = (u32)({rhs});'
        edits.append((definition.end()+m.start(),definition.end()+m.end(),replacement))
        report['changes'].append({'member':member,'offset':offset,'field':fields[0],
                                  'store_instruction':witnesses[0],'source_statement':m[0]})
    for a,b,text in sorted(edits,reverse=True):
        source=source[:a]+text+source[b:]
    report.update(source=source,candidate_sha256=hashlib.sha256(source.encode()).hexdigest())
    return report
