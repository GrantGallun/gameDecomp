"""Bounded decimal formatting buffer and endpoint-alias candidates.

Stack names and available frame span are correspondence hypotheses, not proof
of an original C allocation. The candidate still needs compiler/runtime checks.
"""
import hashlib
import re
from solver import dataflow, project_headers, repair_context


def propose(source,function,assembly):
    report={'source':source,'changes':[],'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
            'scope':'format-bound buffer hypothesis; not original allocation or semantic proof'}
    definition,end=repair_context.definition(source,function)
    mask=project_headers._mask_noncode(source);body=mask[definition.end():end]
    raw=source[definition.end():end]
    if 'M2C_UNK' not in body:return report
    flow=dataflow.analyse(assembly)
    if not flow.graph.instructions:return report
    prologue=flow.graph.instructions[0]
    if prologue.opcode!='addiu' or tuple(map(dataflow.reg,prologue.operands[:2]))!=('sp','sp'):return report
    frame=dataflow.number(prologue.operands[2])
    if frame is None or not -4096<=frame<0:return report
    for root in re.finditer(r'(?m)^[ \t]*(s8|u8)\s+(sp[0-9A-Fa-f]+)\s*;',body):
        typ,name=root.groups();offset=int(name[2:],16);base=frame+offset
        if re.search(r'\b'+name+r'\b',definition[2]) or len(re.findall(r'(?m)^[ \t]*\w+\s+\**\s*'+name+r'\s*[;=\[]',body))!=1:continue
        formats=list(re.finditer(r'\bsprintf\s*\(\s*&'+name+r'\s*,\s*"%([1-9][0-9]*)?(?:\.([0-9]+))?d"\s*,',raw))
        formats=[m for m in formats if body[m.start():].startswith('sprintf')]
        calls=list(re.finditer(r'\bsprintf\s*\(',body))
        if not formats or len(formats)!=len(calls):continue
        size=max(max(int(m[1] or 0),max(10,int(m[2] or 0))+1)+1 for m in formats)
        if size>64 or base<frame+16 or base+size>0:continue
        targets=[c for c in flow.callsites.values() if c.target=='sprintf']
        if len(targets)!=len(formats) or any(c.arguments[0]!=dataflow.Value.address('stack',base) for c in targets):continue
        # Reject fixed accesses outside the proposed region that overlap it.
        if any(a.address and a.address.kind=='address' and a.address.name=='stack'
               and max(a.address.offset,base)<min(a.address.offset+a.width,base+size)
               and (a.width!=1 or a.address.offset<base) for a in flow.accesses.values()):continue
        addresses={v.offset for s in flow.instruction_in.values() for v in s.registers.values()
                   if v and v.kind=='address' and v.name=='stack'}
        aliases=[]
        for decl in re.finditer(r'(?m)^[ \t]*M2C_UNK\s+(sp[0-9A-Fa-f]+)\s*;',body):
            relative=int(decl[1][2:],16)-offset
            if re.search(r'\b'+decl[1]+r'\b',definition[2]):continue
            if not 0<relative<size or base+relative not in addresses:continue
            uses=[m for m in re.finditer(r'\b'+decl[1]+r'\b',body) if not decl.start()<=m.start()<decl.end()]
            if uses and all(re.search(r'&\s*$',body[:m.start()]) for m in uses):
                aliases.append((decl,relative,uses))
        if not aliases:continue
        covered={name,*[d[1] for d,_,_ in aliases]}
        read_aliases={}
        unknown={n for n in re.findall(r'\bsp[0-9A-Fa-f]+\b',body)
                 if offset<=int(n[2:],16)<offset+size and n not in covered}
        for alias in unknown:
            relative=int(alias[2:],16)-offset
            loads=[a for a in flow.accesses.values() if a.is_load and a.address==dataflow.Value.address('stack',base+relative)]
            uses=list(re.finditer(r'\b'+alias+r'\b',body))
            if (not loads or {a.opcode for a in loads}!={'lbu'} or
                re.search(r'(?m)^[ \t]*\w+\s+\**\s*'+alias+r'\s*[;=]',body) or
                re.search(r'\b'+alias+r'\b',definition[2]) or
                any(re.match(r'\s*(?:[+*/&|^-]?=(?!=)|\+\+|--)',body[u.end():]) or re.search(r'[&]|\+\+|--',body[max(0,u.start()-2):u.start()]) for u in uses)):
                break
            read_aliases[alias]=(relative,uses)
        if len(read_aliases)!=len(unknown):continue
        edits=[(root.start(),root.end(),f'    {typ} {name}[{size}];')]
        failed=False
        for use in re.finditer(r'\b'+name+r'\b',body):
            if root.start()<=use.start()<root.end():continue
            address=re.search(r'&\s*$',body[:use.start()])
            if address:edits.append((address.start(),use.end(),name))
            elif re.search(r'\((?:u8|s8)\)\s*$',body[:use.start()]):
                edits.append((use.start(),use.end(),name+'[0]'))
            else:failed=True;break
        if failed:continue
        for decl,relative,uses in aliases:
            edits.append((decl.start(),decl.end(),''))
            for use in uses:
                start=re.search(r'&\s*$',body[:use.start()]).start()
                edits.append((start,use.end(),f'({name} + {relative})'))
        for alias,(relative,uses) in read_aliases.items():
            edits.extend((u.start(),u.end(),f'((u8 *){name})[{relative}]') for u in uses)
        candidate=source
        for a,b,text in sorted(edits,reverse=True):candidate=candidate[:definition.end()+a]+text+candidate[definition.end()+b:]
        report.update(source=candidate,changes=[{'buffer':name,'size':size,'target_base':base,
            'call_instructions':[c.instruction for c in targets],
            'endpoints':{d[1]:relative for d,relative,_ in aliases},
            'byte_reads':{a:relative for a,(relative,_) in read_aliases.items()}}])
        return report
    return report
