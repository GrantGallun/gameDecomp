"""Constant-index stack storage hypotheses with shared scalar alias views."""
import hashlib
import re
from solver import dataflow, global_scalar_view, project_headers, repair_context, stack_layout, type_transaction


def propose(repo,source,function,diagnostics,assembly):
    report={'source':source,'changes':[],'declines':[],
        'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
        'scope':'indexed stack-word storage hypothesis; m2c slot correspondence and C extent are not proven layouts'}
    if 'subscripted value is not an array, pointer, or vector' not in diagnostics:return report
    definition,end=repair_context.definition(source,function)
    mask=project_headers._mask_noncode(source);body=mask[definition.end():end-1]
    if re.search(r'(?m)^\s*#',body):return report
    declarations,_,_=stack_layout._declarations(source,function)
    headers=global_scalar_view.header_text(repo,source)
    macros=set(re.findall(r'(?m)^\s*#\s*define\s+(\w+)',project_headers._mask_comments(headers+'\n'+source)))
    types=set(type_transaction.typedef_names(headers+'\n'+source))|macros|set(stack_layout.ELEMENT)|{'signed','unsigned'}
    flow=dataflow.analyse(assembly)
    instructions=[i for i in flow.graph.instructions if i.opcode not in {'nonmatching','endlabel'}]
    if not instructions:return report
    allocation=next((i for i,ins in enumerate(instructions[:12])
        if dataflow.reg(dataflow._destination(ins) or '')=='sp'),None)
    if allocation is None:return report
    for ins in instructions[:allocation]:
        if ins.opcode.startswith(('b','j')) or any(re.search(r'\bsp\b',dataflow.reg(a)) for a in ins.operands):return report
    first=instructions[allocation];args=tuple(dataflow.reg(a) for a in first.operands)
    if first.opcode!='addiu' or len(args)!=3 or args[:2]!=('sp','sp'):return report
    frame=dataflow.number(args[2])
    if frame is None or not -4096<=frame<0:return report
    for ins in instructions[allocation+1:]:
        if dataflow.reg(dataflow._destination(ins) or '')!='sp':continue
        args=tuple(dataflow.reg(a) for a in ins.operands)
        if ins.opcode!='addiu' or len(args)!=3 or args[:2]!=('sp','sp') or dataflow.number(args[2])!=-frame:return report
    lines=source.splitlines(keepends=True);starts=[0]
    for line in lines:starts.append(starts[-1]+len(line))
    wanted=set()
    pattern=r'^candidate\.c:(\d+):(\d+): error: subscripted value is not an array, pointer, or vector'
    for diagnostic in re.finditer(pattern,diagnostics,re.M):
        number,column=int(diagnostic[1]),int(diagnostic[2])
        if not 1<=number<=len(lines):continue
        line=lines[number-1].rstrip('\r\n')
        excerpt=re.match(r'\n\s*'+str(number)+r' \| (.*)',diagnostics[diagnostic.end():])
        if not excerpt or excerpt[1]!=line or '\t' in line:continue
        for use in re.finditer(r'\b(sp[0-9A-Fa-f]+)\s*\[',mask[starts[number-1]:starts[number]]):
            if use.start()<=column-1<use.end() and definition.end()<=starts[number-1]+use.start()<end-1:wanted.add(use[1])
    edits=[];spans=[]
    for root in sorted(wanted):
        ds=[d for d in declarations if d[2]['name']==root]
        if len(ds)!=1 or root in macros:continue
        start,stop,decl=ds[0];typ=decl['type']
        if typ not in {'s32','u32'} or decl['qual'] or '*' in decl['stars'] or decl['array'] or decl['init']:continue
        if re.search(r'\b'+root+r'\b',definition[2]):continue
        rest=mask[definition.end():start]+' '*(stop-start)+mask[stop:end-1]
        if global_scalar_view.shadowed(rest,root,types):continue
        uses=[m for m in re.finditer(r'\b'+root+r'\b',mask[definition.end():end-1])
              if not start<=definition.end()+m.start()<stop]
        indices=[];changes=[];failed=False
        for use in uses:
            a,b=definition.end()+use.start(),definition.end()+use.end()
            index=re.match(r'\s*\[\s*(0x[0-9A-Fa-f]+|0|[1-9]\d*)\s*\]',mask[b:])
            if mask[:a].rstrip().endswith(('.', '->')):failed=True;break
            if index:indices.append(int(index[1],0));continue
            # &scalar -> &array[0] keeps its element-pointer type and address.
            if re.search(r'(?:[=(,;?:{]|\breturn)\s*&\s*$',mask[:a]) and not re.match(r'\s*[\[.]',mask[b:]):
                changes.append((a,b,root+'[0]'));continue
            failed=True;break
        if failed or not indices:continue
        count=max(indices)+1;offset=int(root[2:],16)
        if not 2<=count<=64 or set(indices)!=set(range(count)) or offset<16 or offset+4*count>-frame:continue
        if any(max(offset,a)<min(offset+4*count,b) for a,b in spans):continue
        witnesses=[]
        for index in range(count):
            peers=[a for a in flow.accesses.values() if a.address==dataflow.Value.address('stack',frame+offset+4*index)]
            if not peers or any(p.opcode not in {'lw','sw'} or p.width!=4 for p in peers):failed=True;break
            witnesses.extend(p.instruction for p in peers)
        if failed:continue
        aliases={m[0] for m in re.finditer(r'\bsp[0-9A-Fa-f]+\b',body)
                 if offset<=int(m[0][2:],16)<offset+4*count}-{root}
        for alias in sorted(aliases):
            alias_ds=[d for d in declarations if d[2]['name']==alias]
            if len(alias_ds)!=1 or alias in macros:failed=True;break
            a,b,d=alias_ds[0];delta=int(alias[2:],16)-offset
            if delta%4 or d['type'] not in {'s32','u32'} or d['qual'] or '*' in d['stars'] or d['array'] or d['init']:failed=True;break
            rest=mask[definition.end():a]+' '*(b-a)+mask[b:end-1]
            if global_scalar_view.shadowed(rest,alias,types):failed=True;break
            changes.append((a,b,''))
            for use in re.finditer(r'\b'+alias+r'\b',mask[definition.end():end-1]):
                x,y=definition.end()+use.start(),definition.end()+use.end()
                if a<=x<b:continue
                if (mask[:x].rstrip().endswith(('.', '->'))
                        or re.search(r'&\s*(?:\(\s*)*$',mask[:x])
                        or re.search(r'\bgoto\s*$',mask[:x])
                        or re.match(r'\s*:(?!:)',mask[y:])
                        or re.match(r'\s*(?:\[|\.|->)',mask[y:])):failed=True;break
                replacement=f'{root}[{delta//4}]'
                if d['type']!=typ:replacement=f"(*({d['type']} *)&{replacement})"
                changes.append((x,y,replacement))
            if failed:break
        if failed:continue
        changes.append((start,stop,f"{decl['i']}{typ} {root}[{count}];\n"))
        edits.extend(changes);spans.append((offset,offset+count*4))
        report['changes'].append(dict(root=root,count=count,type=typ,offset=offset,aliases=sorted(aliases),witnesses=witnesses))
    for a,b,replacement in sorted(edits,reverse=True):report['source']=report['source'][:a]+replacement+report['source'][b:]
    return report
