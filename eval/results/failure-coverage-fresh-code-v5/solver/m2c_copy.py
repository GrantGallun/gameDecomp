"""Bounded lowering candidates for m2c's aligned-copy pseudo-operation.

Not an equivalence certificate: the draft's addresses/length/alignment may be
wrong. Ordinary compiler and differential gates must adjudicate the candidate.
"""
import hashlib
import re
from solver import m2c_byte_view, project_headers, repair_context


def unaligned_loop_layout(assembly):
    """Closed 12-byte-copy loop plus one word tail; evidence, not C storage."""
    from solver import cfg, dataflow
    graph=cfg.build(assembly);ins=graph.instructions;rows=[]
    pattern=['lwl','lwr','addiu','addiu','sw','lwl','lwr','sw','lwl','lwr','bne','sw','lwl','lwr','sw']
    for i in range(2,len(ins)-len(pattern)+1):
        seq=ins[i:i+len(pattern)]
        if [x.opcode for x in seq]!=pattern:continue
        start,limit=ins[i-2:i]
        if start.opcode!='addiu' or limit.opcode!='addiu':continue
        dst,sp,stackoff=start.operands;endreg,src,span=limit.operands
        dst,sp,endreg,src=map(dataflow.reg,(dst,sp,endreg,src))
        stackoff,span=dataflow.number(stackoff),dataflow.number(span)
        if sp!='sp' or stackoff is None or stackoff%4 or span is None or not 0<span<=4092 or span%12 or len({dst,src,endreg,'sp'})!=4:continue
        if tuple(map(dataflow.reg,seq[2].operands[:2]))!=(src,src) or dataflow.number(seq[2].operands[2])!=12:continue
        if tuple(map(dataflow.reg,seq[3].operands[:2]))!=(dst,dst) or dataflow.number(seq[3].operands[2])!=12:continue
        branch=seq[10]
        if tuple(map(dataflow.reg,branch.operands[:2]))!=(src,endreg) or graph.label_to_instruction.get(branch.operands[2])!=i:continue
        valid=True;temps=set()
        for left,right,store,off in [(0,1,4,0),(5,6,7,-8),(8,9,11,-4),(12,13,14,0)]:
            a,b,c=[dataflow.MEMORY.fullmatch(', '.join(seq[n].operands)) for n in (left,right,store)]
            destination_offset=-12 if left==0 else off
            if (not all((a,b,c)) or len({dataflow.reg(x['value']) for x in (a,b,c)})!=1 or
                dataflow.reg(a['base'])!=src or dataflow.reg(b['base'])!=src or dataflow.reg(c['base'])!=dst or
                dataflow.number(a['offset'])!=off or dataflow.number(b['offset'])!=off+3 or dataflow.number(c['offset'])!=destination_offset):
                valid=False;break
            temps.add(dataflow.reg(a['value']))
        if not valid or temps & {src,dst,endreg,'sp'}:continue
        rows.append({'first_instruction':i,'last_instruction':i+14,'stack_offset':stackoff,
            'loop_bytes':span,'tail_bytes':4,'total_bytes':span+4,'alignment':4,
            'source_register':src,'destination_register':dst})
    return {'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),'copies':rows,
        'scope':'target copy extent and aligned destination witness; not C object extent or source alias identity'}


def destination_storage(source,function,assembly):
    """Stack-name correspondence hypothesis for a witnessed copy destination."""
    from solver import cfg,dataflow
    evidence=unaligned_loop_layout(assembly);mask=project_headers._mask_noncode(source)
    definition,end=repair_context.definition(source,function);body=mask[definition.end():end-1]
    graph=cfg.build(assembly);changes=[];edits=[]
    for copy in evidence['copies']:
        root='sp'+format(copy['stack_offset'],'X');size=copy['total_bytes']
        decls=list(re.finditer(r'(?m)^[ \t]*M2C_UNK\s+'+root+r'\s*;',body))
        if len(decls)!=1 or re.search(r'\b'+root+r'\b',definition[2]):continue
        decl=decls[0];local_edits=[(decl.start(),decl.end(),f'    u32 {root}[{size//4}];')]
        uses=[u for u in re.finditer(r'\b'+root+r'\b',body) if not decl.start()<=u.start()<decl.end()]
        if not uses or any(not re.search(r'&\s*$',body[:u.start()]) for u in uses):continue
        # Do not merge separately declared neighboring objects into the buffer.
        names=set(re.findall(r'\bsp[0-9A-Fa-f]+\b',body));aliases={}
        valid=True
        for name in names-{root}:
            offset=int(name[2:],16)
            if not copy['stack_offset']<offset<copy['stack_offset']+size:continue
            if re.search(r'(?m)^\s*\w+\s+\**\s*'+name+r'\s*[;=\[]',body) or re.search(r'\b'+name+r'\b',definition[2]):valid=False;break
            loads=[i for i in graph.instructions if i.opcode=='lbu' and (m:=dataflow.MEMORY.fullmatch(', '.join(i.operands))) and dataflow.reg(m['base'])=='sp' and dataflow.number(m['offset'])==offset]
            alias_uses=list(re.finditer(r'\b'+name+r'\b',body))
            if not loads or any(re.search(r'(?:&|\+\+|--)\s*$',body[:u.start()]) or re.match(r'\s*(?:[+\-*/%&|^]?=(?!=)|<<=|>>=|\+\+|--)',body[u.end():]) for u in alias_uses):valid=False;break
            relative=offset-copy['stack_offset'];aliases[name]=relative
            local_edits += [(u.start(),u.end(),f'((u8 *){root})[{relative}]') for u in alias_uses]
        if not valid:continue
        for u in uses:
            address=re.search(r'&\s*$',body[:u.start()])
            local_edits.append((address.start(),u.end(),f'((u8 *){root})'))
        # Unknown destination cursors must have one seed and only the witnessed
        # byte step / word-store offsets. Never retype an escaping pointer.
        cursors=[]
        for cursor_decl in re.finditer(r'(?m)^[ \t]*M2C_UNK\s*\*\s*(\w+)\s*;',body):
            name=cursor_decl[1]
            if re.search(r'\b'+name+r'\b',definition[2]):continue
            seeds=list(re.finditer(r'\b'+name+r'\s*=\s*&\s*'+root+r'\s*;',body))
            steps=list(re.finditer(r'\b'+name+r'\s*\+=\s*(?:12|0x[Cc])\s*;',body))
            stores=list(re.finditer(r'M2C_FIELD\(\s*'+name+r'\s*,\s*s32\s*\*\s*,\s*(-?(?:0x[0-9a-fA-F]+|[0-9]+))\s*\)\s*=(?!=)',body))
            if len(seeds)!=1 or len(steps)!=1 or len(stores)!=4:continue
            if sorted(int(m[1],0) for m in stores)!=[-12,-8,-4,0]:continue
            allowed=[cursor_decl,*seeds,*steps,*stores]
            if any(not any(m.start()<=u.start()<m.end() for m in allowed)
                   for u in re.finditer(r'\b'+name+r'\b',body)):continue
            if not cursor_decl.end()<=seeds[0].start()<steps[0].start()<min(m.start() for m in stores):continue
            local_edits.append((cursor_decl.start(),cursor_decl.end(),f'    u8 *{name};'))
            cursors.append(name)
        edits+=local_edits;changes.append({'root':root,'bytes':size,'aliases':aliases,'copy':copy,'byte_cursors':cursors})
    if len(changes)>1:return source,{'changes':[],'reason':'multiple copy destinations require disjointness proof'}
    candidate=source
    for a,b,text in sorted(edits,reverse=True):candidate=candidate[:definition.end()+a]+text+candidate[definition.end()+b:]
    return candidate,{'changes':changes,'evidence':evidence,'scope':'aligned storage candidate; stack-name alias correspondence is not proof'}


def source_cursor(source, function, assembly):
    """Closed unaligned-copy cursor/endpoint hypothesis, not union layout."""
    evidence=unaligned_loop_layout(assembly)
    if len(evidence['copies'])!=1:return source,{'changes':[]}
    copy=evidence['copies'][0]
    definition,end=repair_context.definition(source,function)
    body=project_headers._mask_noncode(source)[definition.end():end-1]
    edits=[];changes=[]
    for decl in re.finditer(r'(?m)^[ \t]*union\s+_anonymous\s*\*\s*(\w+)\s*;',body):
        name=decl[1]
        if re.search(r'\b'+name+r'\b',definition[2]):continue
        seeds=list(re.finditer(r'\b'+name+r'\s*=\s*(\w+)\s*;',body))
        steps=list(re.finditer(r'\b'+name+r'\s*\+=\s*(?:12|0x[Cc])\s*;',body))
        endpoints=list(re.finditer(r'\b(\w+)\s*=\s*&'+name+r'->\w+\[(\d+)\]\s*;',body))
        direct=list(re.finditer(r'M2C_UNALIGNED32\(\*'+name+r'\)',body))
        fields=list(re.finditer(r'M2C_UNALIGNED32\(M2C_FIELD\('+name+r',\s*M2C_UNK\s*\*,\s*(-?(?:0x[0-9a-fA-F]+|\d+))\)\)',body))
        if len(seeds)!=1 or len(steps)!=1 or len(endpoints)!=1 or len(direct)!=1 or len(fields)!=3:continue
        if sorted(int(m[1],0) for m in fields)!=[-8,-4,0]:continue
        endpoint=endpoints[0];limit=endpoint[1]
        if int(endpoint[2])*4!=copy['loop_bytes']:continue
        limit_decls=list(re.finditer(r'(?m)^[ \t]*u32\s*\*\s*'+limit+r'\s*;',body))
        comparisons=list(re.finditer(r'\b'+name+r'\s*!=\s*'+limit+r'\b',body))
        if len(limit_decls)!=1 or len(comparisons)!=1 or re.search(r'\b'+limit+r'\b',definition[2]):continue
        allowed=[decl,*seeds,*steps,*endpoints,*direct,*fields,*comparisons]
        if any(not any(m.start()<=u.start()<m.end() for m in allowed) for u in re.finditer(r'\b'+name+r'\b',body)):continue
        if any(not any(m.start()<=u.start()<m.end() for m in [limit_decls[0],endpoint,*comparisons]) for u in re.finditer(r'\b'+limit+r'\b',body)):continue
        if not decl.end()<=seeds[0].start()<endpoint.start()<direct[0].start()<steps[0].start():continue
        edits.extend([(decl.start(),decl.end(),f'    u8 *{name};'),
            (limit_decls[0].start(),limit_decls[0].end(),f'    u8 *{limit};'),
            (seeds[0].start(),seeds[0].end(),f'{name} = (u8 *){seeds[0][1]};'),
            (endpoint.start(),endpoint.end(),f'{limit} = {name} + {copy["loop_bytes"]};')])
        changes.append({'cursor':name,'endpoint':limit,'bytes':copy['loop_bytes']})
    if len(changes)!=1:return source,{'changes':[]}
    for a,b,text in sorted(edits,reverse=True):source=source[:definition.end()+a]+text+source[definition.end()+b:]
    return source,{'changes':changes,'evidence':evidence,'scope':'closed source-shape correspondence hypothesis; no anonymous union layout inferred'}


def stack_byte_steps(graph, offset):
    """Local def/use witness through a backedge's delay slot, independent of schedule."""
    from solver import dataflow
    ins=graph.instructions;rows=[]
    for i,first in enumerate(ins):
        load=dataflow.MEMORY.fullmatch(', '.join(first.operands))
        if first.opcode!='lw' or not load or dataflow.reg(load['base'])!='sp' or dataflow.number(load['offset'])!=offset:continue
        initial=dataflow.reg(load['value'])
        if initial in {'sp','zero'}:continue
        values={initial:0};stores=0;branch=None;valid=True
        for j in range(i+1,min(i+17,len(ins))):
            x=ins[j];op=x.opcode;args=x.operands
            if j in graph.label_to_instruction.values():valid=False;break
            if op=='bnez' and branch is None:
                if graph.label_to_instruction.get(args[-1],i+1)>i:valid=False;break
                branch=j
                continue
            if op=='nop':pass
            elif op in {'sw','sh','sb'}:
                m=dataflow.MEMORY.fullmatch(', '.join(args))
                if not m or dataflow.reg(m['base'])!='sp':valid=False;break
                address=dataflow.number(m['offset']);width={'sw':4,'sh':2,'sb':1}[op]
                if address is None:valid=False;break
                if address<offset+4 and address+width>offset:
                    if op!='sw' or address!=offset or values.get(dataflow.reg(m['value']))!=1:valid=False;break
                    stores+=1
            elif op in {'lw','addiu','slt','slti','sll','addu','move'}:
                dest=dataflow.reg(args[0])
                if dest in {'sp','zero'}:valid=False;break
                value=None
                if op=='addiu' and dataflow.reg(args[1]) in values:
                    step=dataflow.number(args[2])
                    if step is not None:value=values[dataflow.reg(args[1])]+step
                elif op=='move':value=values.get(dataflow.reg(args[1]))
                values.pop(dest,None)
                if value is not None:values[dest]=value
            else:valid=False;break
            if branch is not None:
                if valid and stores==1:rows.append(i)
                break
    return rows


def feeding_cursor(source, function, assembly, copy_cursors):
    """Byte-stride stack cursor feeding a recognized unaligned copy."""
    from solver import cfg,dataflow
    graph=cfg.build(assembly);ins=graph.instructions
    definition,end=repair_context.definition(source,function)
    body=project_headers._mask_noncode(source)[definition.end():end-1]
    edits=[];changes=[]
    for decl in re.finditer(r'(?m)^[ \t]*union\s+_anonymous\s*\*\s*(sp[0-9A-Fa-f]+)\s*;',body):
        name=decl[1];offset=int(name[2:],16)
        if re.search(r'\b'+name+r'\b',definition[2]):continue
        seeds=list(re.finditer(r'\b'+name+r'\s*=\s*&(\w+)\s*;',body))
        steps=list(re.finditer(r'\b'+name+r'\s*\+=\s*1\s*;',body))
        feeds=[m for m in re.finditer(r'\b(\w+)\s*=\s*\(u8\s*\*\)'+name+r'\s*;',body) if m[1] in copy_cursors]
        if not seeds or len({m[1] for m in seeds})!=1 or len(steps)!=1 or len(feeds)!=1:continue
        allowed=[decl,*seeds,*steps,*feeds]
        if any(not any(m.start()<=u.start()<m.end() for m in allowed) for u in re.finditer(r'\b'+name+r'\b',body)):continue
        witnesses=stack_byte_steps(graph,offset)
        if len(witnesses)!=1:continue
        edits.append((decl.start(),decl.end(),f'    u8 *{name};'))
        edits.extend((m.start(),m.end(),f'{name} = (u8 *)&{m[1]};') for m in seeds)
        changes.append({'cursor':name,'stack_offset':offset,'byte_step':1,'target_instruction':witnesses[0]})
    for a,b,text in sorted(edits,reverse=True):source=source[:definition.end()+a]+text+source[definition.end()+b:]
    return source,{'changes':changes,'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
        'scope':'stack-name and closed-use correspondence hypothesis; no header layout altered'}


def indexed_stack_read(source, function, assembly, storage):
    """Bounded copy-out loop into reconstructed storage; candidate only."""
    from solver import cfg,dataflow
    graph=cfg.build(assembly);ins=graph.instructions;witnesses=[]
    pattern=['sw','lw','lw','addu','lbu','sb','lw','lw','addiu','slti','addiu','sw','bnez','sw']
    def mem(x):return dataflow.MEMORY.fullmatch(', '.join(x.operands))
    def canonical(x):
        return (x.opcode,tuple(re.sub(r'-?0x[0-9a-fA-F]+',lambda m:str(int(m[0],0)),p.replace('$','')) for p in x.operands))
    for i in range(len(ins)-13):
        s=ins[i:i+14]
        if [x.opcode for x in s]!=pattern:continue
        if graph.label_to_instruction.get(s[12].operands[-1])!=i+1:continue
        if not all(mem(s[n]) for n in [0,1,2,4,5,6,7,11,13]):continue
        slot=dataflow.number(mem(s[0])['offset']);dstslot=dataflow.number(mem(s[2])['offset'])
        off=dataflow.number(mem(s[4])['offset']);bound=dataflow.number(s[9].operands[2])
        if None in (slot,dstslot,off,bound) or not 0<bound<=4096:continue
        a,b,c,d,e,f,g,h=[dataflow.reg(s[n].operands[0]) for n in [1,2,3,6,7,8,9,10]]
        if len({a,b,c,d,e,f,g,h,'sp','zero'})!=10:continue
        expected=cfg.build(f'''sw zero, {slot}(sp)
lw {a}, {slot}(sp)
lw {b}, {dstslot}(sp)
addu {c}, sp, {a}
lbu {c}, {off}({c})
sb {c}, 0({b})
lw {d}, {slot}(sp)
lw {e}, {dstslot}(sp)
addiu {f}, {d}, 1
slti {g}, {f}, {bound}
addiu {h}, {e}, 1
sw {f}, {slot}(sp)
bnez {g}, {s[12].operands[-1]}
sw {h}, {dstslot}(sp)''').instructions
        if [canonical(x) for x in s]!=[canonical(x) for x in expected]:continue
        witnesses.append({'index_slot':slot,'offset':off,'bound':bound,'first_instruction':i})
    definition,end=repair_context.definition(source,function)
    body=project_headers._mask_noncode(source)[definition.end():end-1];edits=[];changes=[]
    for w in witnesses:
        index='sp'+format(w['index_slot'],'X')
        eligible=[r for r in storage if 0<=w['offset']-r['copy']['stack_offset'] and w['offset']-r['copy']['stack_offset']+w['bound']<=r['bytes']]
        if len(eligible)!=1:continue
        root=eligible[0]['root'];relative=w['offset']-eligible[0]['copy']['stack_offset']
        loop=re.compile(r'\b'+index+r'\s*=\s*0;\s*do\s*\{\s*\*(\w+)\s*=\s*(M2C_FIELD\(\(sp\s*\+\s*'+index+r'\),\s*u8\s*\*,\s*(0x[0-9a-fA-F]+|\d+)\));\s*(\w+)\s*=\s*'+index+r'\s*\+\s*1;\s*'+index+r'\s*=\s*\4;\s*\1\s*\+=\s*1;\s*\}\s*while\s*\(\4\s*<\s*(0x[0-9a-fA-F]+|\d+)\);')
        matches=[m for m in loop.finditer(body) if int(m[3],0)==w['offset'] and int(m[5],0)==w['bound']]
        if len(matches)!=1 or re.search(r'\bsp\b',definition[2]):continue
        m=matches[0]
        edits.append((m.start(2),m.end(2),f'((u8 *){root})[{relative} + {index}]'))
        changes.append({**w,'root':root,'relative_offset':relative})
    for a,b,text in sorted(set(edits),reverse=True):source=source[:definition.end()+a]+text+source[definition.end()+b:]
    return source,{'changes':changes,'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
        'scope':'matching bounded source/target copy-out hypothesis; not universal semantic proof'}


def propose(source, function):
    report = {'source': source, 'changes': [],
              'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
              'scope': 'm2c aligned-copy candidate, not semantic proof'}
    mask = project_headers._mask_noncode(source)
    definition, end = repair_context.definition(source, function)
    if re.search(r'\bM2C_MEMCPY_ALIGNED\b', mask[:definition.end()]+mask[end:]):
        return report
    edits = []
    for call in re.finditer(r'(?m)^[ \t]*M2C_MEMCPY_ALIGNED\s*\(', mask[definition.end():end-1]):
        start = definition.end()+call.start()
        opening = definition.end()+call.end()-1
        closing = m2c_byte_view.closing(mask, opening)
        if closing < opening or not re.match(r'\s*;', mask[closing+1:]):
            continue
        args = m2c_byte_view.arguments(source[opening+1:closing])
        if len(args)!=3 or not re.fullmatch(r'(?:0x[0-9a-fA-F]+|[1-9][0-9]*)', args[2]):
            continue
        size = int(args[2], 0)
        if not 0<size<=4096 or size%4:
            continue
        # Pure address expressions only; avoid choosing an evaluation order for
        # calls, increments, volatile accesses, or assignments in arguments.
        if any(re.search(r'\+\+|--|=|\bvolatile\b|\b\w+\s*\(', arg) or
               not re.fullmatch(r'[\w\s()*&+<>.-]+', arg) for arg in args[:2]):
            continue
        names = ['m2c_copy_dst', 'm2c_copy_src', 'm2c_copy_i']
        if any(re.search(r'\b'+name+r'\b', mask) for name in names):
            continue
        dest, src = args[:2]
        # GNU void-pointer byte offsets are invalid in IDO. Lower only the
        # explicit pointer-load view, leaving ordinary typed arithmetic alone.
        dest = re.sub(r'\(\*\(void\s*\*\*\)', '(*(unsigned char **)', dest)
        replacement = ('{\n'
            f'    unsigned int *m2c_copy_dst = (unsigned int *)({dest});\n'
            f'    const unsigned int *m2c_copy_src = (const unsigned int *)({src});\n'
            '    unsigned int m2c_copy_i;\n'
            f'    for (m2c_copy_i = 0; m2c_copy_i < {size//4}; ++m2c_copy_i)\n'
            '        m2c_copy_dst[m2c_copy_i] = m2c_copy_src[m2c_copy_i];\n'
            '}')
        stop = closing+1+re.match(r'\s*;', mask[closing+1:]).end()
        edits.append((start,stop,replacement))
        report['changes'].append({'start':start,'end':stop,'bytes':size,
                                  'before':source[start:stop],'after':replacement})
    if len(edits)>32:
        report['changes']=[]
        return report
    for start,stop,replacement in reversed(edits):
        report['source']=report['source'][:start]+replacement+report['source'][stop:]
    return report
