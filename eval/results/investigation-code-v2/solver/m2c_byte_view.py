"""Experimental lowering of fresh m2c field macros to typed byte accesses.

No layout or C-type facts are created. Global pointer views and inferred bare
dereference types are candidate hypotheses requiring compiler/runtime validation.
"""
import hashlib
import re
from solver import project_headers, repair_context


def unknown_local_cursors(source, function, *, header_declarations=None):
    """Closed byte-cursor C hypotheses; never inferred storage/extent facts."""
    masked = project_headers._mask_noncode(source)
    definition, end = repair_context.definition(source, function)
    start = definition.end()
    body = masked[start:end-1]
    report = {'source': source, 'changes': [], 'declines': [],
        'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'authority': 'closed explicit-byte-use source hypothesis; compiler/runtime adjudicate'}
    edits = []
    for decl in re.finditer(r'(?m)^[ \t]*M2C_UNK\s*\*\s*(\w+)\s*;', body):
        name = decl[1]
        if re.search(r'\b'+name+r'\b', definition[2]):
            continue
        allowed = [(decl.start(), decl.end())]
        assignments = list(re.finditer(r'(?m)^[ \t]*'+name+r'\s*=\s*(&\s*(\w+))\s*;', body))
        steps = list(re.finditer(r'(?m)^[ \t]*'+name+r'\s*\+=\s*(?:0x[0-9a-fA-F]+|[1-9][0-9]*)\s*;', body))
        fields = list(re.finditer(r'\(\*\(u8\s*\*\)\(\(u8\s*\*\)\('+name+r'\)\s*\+\s*(?:0x[0-9a-fA-F]+|[0-9]+)\)\)', body))
        if len(assignments) != 1 or not steps or not fields:
            report['declines'].append({'local': name, 'reason': 'requires one address seed, literal steps and explicit byte fields'})
            continue
        assignment = assignments[0]
        base = assignment[2]
        # Only explicit nonvolatile primitive extern objects. Do not invent a
        # declaration, accept another unknown type, or reinterpret pointer data.
        globals_ = list(re.finditer(r'(?m)^extern\s+(?:s8|u8|s16|u16|s32|u32)\s+'+base+r'\s*(?:\[\s*(?:[0-9]+)?\s*\])?\s*;', masked[:definition.start()]))
        header_seeds = sorted(set((header_declarations or {}).get(base, [])))
        header_seed = (header_seeds[0] if len(header_seeds)==1 and re.fullmatch(
            r'extern\s+(?!M2C_UNK\b)(?:struct\s+|union\s+)?[A-Za-z_]\w*\s+'
            +re.escape(base)+r'\s*(?:\[\s*\d*\s*\]\s*)*;',header_seeds[0]) else None)
        if len(globals_) != 1 and not header_seed:
            report['declines'].append({'local': name, 'reason': 'address seed lacks unique primitive object declaration'})
            continue
        if re.search(r'(?m)^[ \t]*\w+\s+\**\s*'+re.escape(base)+r'\s*[;=]',body) or re.search(r'\b'+re.escape(base)+r'\b',definition[2]):
            continue
        allowed += [(m.start(), m.end()) for m in assignments+steps+fields]
        # Explicit integer-address comparisons do not depend on pointee type.
        comparisons = list(re.finditer(r'\(u32\)\s*'+name+r'\s*(?:<|<=|>|>=|==|!=)\s*\(u32\)\s*\w+\b',body))
        allowed += [(m.start(),m.end()) for m in comparisons]
        if any(not any(a <= use.start() < b for a,b in allowed) for use in re.finditer(r'\b'+name+r'\b', body)):
            report['declines'].append({'local': name, 'reason': 'nonclosed cursor uses'})
            continue
        if assignment.start() < decl.end() or any(m.start() < assignment.end() for m in steps+fields):
            continue
        edits.append((start+decl.start(), start+decl.end(), '    u8 *'+name+';'))
        edits.append((start+assignment.start(1), start+assignment.end(1), '(u8 *)'+source[start+assignment.start(1):start+assignment.end(1)]))
        report['changes'].append({'local': name, 'address_seed': base,
            'header_seed_declaration': header_seed,
            'source_constraints': [source[start+a:start+b] for a,b in allowed],
            'scope': 'candidate byte cursor; no global layout or extent claim'})
    for a,b,replacement in sorted(edits, reverse=True):
        source = source[:a]+replacement+source[b:]
    report.update(source=source, candidate_sha256=hashlib.sha256(source.encode()).hexdigest())
    return report


def closing(text, at):
    depth=0
    for i in range(at,len(text)):
        if text[i]=='(': depth+=1
        elif text[i]==')':
            depth-=1
            if depth==0: return i
    raise ValueError('unbalanced expression')


def arguments(text):
    parts=[]
    start=depth=0
    for i,ch in enumerate(text):
        if ch=='(': depth+=1
        elif ch==')': depth-=1
        elif ch==',' and depth==0:
            parts.append(text[start:i].strip()); start=i+1
    return parts+[text[start:].strip()]


def byte_word_expression(base, offset):
    terms=[f'((u32)((u8 *){base})[{offset+i}]'+(f' << {24-i*8})' if i<3 else ')') for i in range(4)]
    return '(s32)('+' | '.join(terms)+')'


def named_record_word_reads(source, function, assembly):
    """Replace an aggregate-to-word cast with a witnessed named four-byte read.

    A target LW is about bytes, not a C aggregate conversion. Do not unwrap
    bare value macros here: their consumer may require a wider store too.
    """
    from solver import dataflow
    definition,end=repair_context.definition(source,function)
    mask=project_headers._mask_noncode(source)
    pattern=(r'M2C_UNALIGNED32\(\s*\(s32\)\s*M2C_FIELD\(\s*&(\w+)\s*,\s*'
             r'(?:struct\s+)?\w+\s*\*\s*,\s*(0x[0-9a-fA-F]+|[0-9]+)\s*\)\s*\)')
    matches=list(re.finditer(pattern,mask[definition.end():end-1]))
    if not matches:return source,[]
    flow=dataflow.analyse(assembly)
    edits=[];rows=[]
    for match in matches:
        name,offset=match[1],int(match[2],0)
        # Reject shadows: the spelling must denote the assembly's named global,
        # not a local variable or a parameter sharing its name.
        if re.search(r'\b'+name+r'\b',definition[2]) or re.search(
                r'(?m)^[ \t]*(?:struct\s+)?\w+\s+\**\s*'+name+r'\s*(?:[;=\[])',mask[definition.end():end-1]):
            continue
        witnesses=[i for i,a in flow.accesses.items() if a.opcode=='lw' and a.is_load
                   and a.width==4 and a.address==dataflow.Value.address(name,offset)]
        if not witnesses:continue
        edits.append((definition.end()+match.start(),definition.end()+match.end(),
                      byte_word_expression('&'+name,offset)))
        rows.append({'symbol':name,'offset':offset,'width':4,'target_loads':witnesses,
                     'authority':'named target word read; byte view, not aggregate cast or original C type'})
    for a,b,replacement in sorted(edits,reverse=True):source=source[:a]+replacement+source[b:]
    return source,rows


def unaligned32_reads(source, function, assembly):
    """Bytewise big-endian read candidates, never aligned word dereferences."""
    from solver import cfg, dataflow
    graph=cfg.build(assembly)
    offsets={}
    for left,right in zip(graph.instructions,graph.instructions[1:]):
        if left.opcode!='lwl' or right.opcode!='lwr':continue
        a=dataflow.MEMORY.fullmatch(', '.join(left.operands));b=dataflow.MEMORY.fullmatch(', '.join(right.operands))
        if a and b and a['base']==b['base'] and a['value']==b['value'] and int(b['offset'],0)==int(a['offset'],0)+3:
            offsets.setdefault(int(a['offset'],0),[]).append(left.index)
    definition,end=repair_context.definition(source,function)
    masked=project_headers._mask_noncode(source);edits=[];rows=[]
    for match in re.finditer(r'\bM2C_UNALIGNED32\s*\(',masked[definition.end():end]):
        at=definition.end()+match.start();opening=definition.end()+match.end()-1
        stop=closing(masked,opening);arg=masked[opening+1:stop].strip()
        field=re.fullmatch(r'M2C_FIELD\((\w+),\s*M2C_UNK\s*\*,\s*(-?(?:0x[0-9a-fA-F]+|[0-9]+))\)',arg)
        direct=re.fullmatch(r'\*\s*(\w+)',arg)
        if field:base,offset=field[1],int(field[2],0)
        elif direct:base,offset=direct[1],0
        else:continue
        if offset not in offsets or re.match(r'\s*=(?!=)',masked[stop+1:]):continue
        replacement=byte_word_expression(base, offset)
        edits.append((at,stop+1,replacement));rows.append({'base':base,'offset':offset,'target_pairs':offsets[offset]})
    for a,b,text in sorted(edits,reverse=True):source=source[:a]+text+source[b:]
    return source,rows


def unknown_address_externs(source, function, assembly):
    """Byte-address views for unknown externs used only in witnessed shifted call arguments."""
    from solver import cfg,dataflow
    definition,end=repair_context.definition(source,function)
    mask=project_headers._mask_noncode(source);ins=cfg.build(assembly).instructions
    edits=[];changes=[]
    for decl in re.finditer(r'(?m)^extern M2C_UNK (\w+);',mask[:definition.start()]):
        name=decl[1]
        uses=list(re.finditer(r'\b'+name+r'\b',mask))
        calls=list(re.finditer(r'\b(\w+)\(\s*[^,();]+,\s*(\(\w+->\w+\s*<<\s*(\d+)\)\s*\+\s*&'+name+r')\s*\)',mask[definition.end():end-1]))
        if len(calls)!=1 or len(uses)!=2:continue
        call=calls[0];shift=int(call[3])
        if not 0<=shift<32:continue
        witnesses=[]
        for i in range(len(ins)-4):
            hi,lo,scale,add,jump=ins[i:i+5]
            if [x.opcode for x in (hi,lo,scale,add,jump)]!=['lui','addiu','sll','addu','jal']:continue
            base=dataflow.reg(hi.operands[0]);scaled=dataflow.reg(scale.operands[0])
            if base in {'sp','zero','a1'} or scaled in {base,'sp','zero'}:continue
            if hi.operands[1]!=f'%hi({name})' or lo.operands[2]!=f'%lo({name})':continue
            if tuple(map(dataflow.reg,lo.operands[:2]))!=(base,base):continue
            if dataflow.number(scale.operands[2])!=shift:continue
            if dataflow.reg(add.operands[0])!='a1' or set(map(dataflow.reg,add.operands[1:]))!={base,scaled}:continue
            if jump.operands[0]!=call[1]:continue
            witnesses.append(i)
        if len(witnesses)!=1:continue
        edits.append((decl.start(),decl.end(),f'extern u8 {name}[];'))
        at=definition.end()+call.start(2)
        address=re.search(r'&'+name+r'\b',mask[at:definition.end()+call.end(2)])
        edits.append((at+address.start(),at+address.end(),name))
        changes.append({'symbol':name,'call':call[1],'shift':shift,'target_instruction':witnesses[0],
            'scope':'byte-address candidate; no array extent or source-field/register identity proven'})
    for a,b,text in sorted(edits,reverse=True):source=source[:a]+text+source[b:]
    return source,changes


def unaligned32_field_stores(source, function, assembly):
    """Big-endian four-byte store candidates with a live SWL/SWR witness.

    Evaluate the source word once before byte writes (including overlapping
    copies). Do not invent a scalar type for the destination object.
    """
    from solver import cfg, dataflow
    graph = cfg.build(assembly)
    pairs = {}
    for i, left in enumerate(graph.instructions):
        first = dataflow.MEMORY.fullmatch(', '.join(left.operands))
        if left.opcode != 'swl' or not first:
            continue
        offset = dataflow.number(first['offset'])
        if offset is None:
            continue
        protected = {dataflow.reg(first['value']), dataflow.reg(first['base'])}
        for right in graph.instructions[i+1:i+5]:
            if graph.instruction_to_block[right.index] != graph.instruction_to_block[i]:
                break
            second = dataflow.MEMORY.fullmatch(', '.join(right.operands))
            if (right.opcode == 'swr' and second and first['value'] == second['value']
                    and first['base'] == second['base']
                    and dataflow.number(second['offset']) == offset+3):
                pairs.setdefault(offset, []).append([i, right.index])
                break
            if (right.opcode not in dataflow.WRITES_FIRST | {'nop'}
                    or dataflow._destination(right) in protected):
                break
    definition, end = repair_context.definition(source, function)
    mask = project_headers._mask_noncode(source)
    edits, changes = [], []
    for match in re.finditer(r'(?m)^[ \t]*(?:M2C_FIELD\s*\(|\*(\w+)\b)', mask[definition.end():end-1]):
        at = definition.end()+match.start()
        if match[1]:
            base, offset_text = match[1], '0'
            if len(re.findall(r'(?m)^[ \t]*M2C_UNK\s*\*\s*'+base+r'\s*;', mask[definition.end():end-1])) != 1:
                continue
            stop = definition.end()+match.end()-1
        else:
            opening = definition.end()+match.end()-1
            stop = closing(mask, opening)
            fields = arguments(mask[opening+1:stop])
            if len(fields) != 3 or not re.fullmatch(r'M2C_UNK\s*\*', fields[1]):
                continue
            base, _, offset_text = fields
        offset = dataflow.number(offset_text)
        if offset not in pairs:
            continue
        # Side-effect-free byte-address hypothesis; no calls, updates, reads
        # through explicit dereferences, or arbitrary C expressions admitted.
        if (not re.fullmatch(r'[\w\s()+.*&>\-]+', base)
                or re.search(r'\w\s*\(|\+\+|--|(?<!-)>(?!\w)', base)
                or re.search(r'(?<!\w)\*\s*[A-Za-z_]', base)):
            continue
        rhs = re.match(r'\s*=\s*M2C_UNALIGNED32\s*\(', mask[stop+1:])
        if not rhs:
            continue
        rhs_open = stop+rhs.end()
        rhs_stop = closing(mask, rhs_open)
        tail = re.match(r'\s*;', mask[rhs_stop+1:])
        if not tail:
            continue
        word = source[stop+1:rhs_stop+1].split('=', 1)[1].strip()
        suffix = 0
        while re.search(r'\b_m2c_store_'+str(suffix)+r'_(?:word|dst)\b', mask):
            suffix += 1
        prefix = '_m2c_store_'+str(suffix)
        replacement = '{ u32 '+prefix+'_word = (u32)('+word+'); '
        replacement += 'u8 *'+prefix+'_dst = (u8 *)('+base+') + '+offset_text+'; '
        replacement += ' '.join(prefix+f'_dst[{n}] = (u8)('+prefix+f'_word >> {24-8*n});' for n in range(4))+' }'
        edits.append((at, rhs_stop+1+tail.end(), replacement))
        changes.append({'base':base,'offset':offset,'target_store_pairs':pairs[offset],
                        'scope':'word width/offset witness; source address correspondence remains a hypothesis'})
    for a, b, replacement in sorted(edits, reverse=True):
        source = source[:a]+replacement+source[b:]
    return source, changes


def copied_word_temporaries(source, function, reads):
    """Closed unknown scalar holding one witnessed bytewise word read."""
    definition,end=repair_context.definition(source,function)
    mask=project_headers._mask_noncode(source);body=mask[definition.end():end-1]
    expressions={byte_word_expression(r['base'],r['offset']) for r in reads}
    edits=[];changes=[]
    for decl in re.finditer(r'(?m)^[ \t]*M2C_UNK\s+(\w+)\s*;',body):
        name=decl[1]
        assignments=list(re.finditer(r'(?m)^[ \t]*'+name+r'\s*=\s*([^;]+);',body))
        uses=list(re.finditer(r'\bM2C_UNALIGNED32\(\s*'+name+r'\s*\)',body))
        if len(assignments)!=1 or assignments[0][1] not in expressions or not uses:
            continue
        assignment=assignments[0]
        if assignment.start()<decl.end() or any(u.start()<assignment.end() for u in uses):
            continue
        allowed=[decl.span(),assignment.span(),*(u.span() for u in uses)]
        if any(not any(a<=u.start()<b for a,b in allowed) for u in re.finditer(r'\b'+name+r'\b',body)):
            continue
        edits.append((decl.start(),decl.end(),'    u32 '+name+';'))
        edits.extend((u.start(),u.end(),name) for u in uses)
        changes.append({'local':name,'kind':'copied-word-temporary','authority':'closed witnessed read value, not original C type'})
    for a,b,text in sorted(edits,reverse=True):
        a+=definition.end();b+=definition.end();source=source[:a]+text+source[b:]
    return source,changes


def closed_copy_cursors(source, function, assembly):
    """Byte-cursor hypothesis only when every use has explicit byte units."""
    from solver import cfg,dataflow
    definition,end=repair_context.definition(source,function)
    mask=project_headers._mask_noncode(source);body=mask[definition.end():end-1]
    steps={dataflow.number(i.operands[2]) for i in cfg.build(assembly).instructions
           if i.opcode=='addiu' and len(i.operands)==3 and i.operands[0]==i.operands[1]}
    pointer_seeds=set(re.findall(r'(?m)^[ \t]*void\s*\*\s*(\w+)\s*;',body))
    edits=[];changes=[]
    for decl in re.finditer(r'(?m)^[ \t]*M2C_UNK\s*\*\s*(\w+)\s*;',body):
        name=decl[1]
        seeds=list(re.finditer(r'(?m)^[ \t]*'+name+r'\s*=\s*(\w+)\s*\+\s*(0x[0-9a-fA-F]+|[0-9]+)\s*;',body))
        moves=list(re.finditer(r'(?m)^[ \t]*'+name+r'\s*([+-])=\s*(0x[0-9a-fA-F]+|[0-9]+)\s*;',body))
        views=list(re.finditer(r'\(u8\s*\*\)\s*(?:\('+name+r'\)|'+name+r'\b)',body))
        if len(seeds)!=1 or seeds[0][1] not in pointer_seeds or not moves or not views:
            continue
        if any((1 if m[1]=='+' else -1)*int(m[2],0) not in steps for m in moves):
            continue
        allowed=[decl.span(),seeds[0].span(),*(m.span() for m in moves+views)]
        if seeds[0].start()<decl.end() or any(m.start()<seeds[0].end() for m in moves+views):
            continue
        if any(not any(a<=u.start()<b for a,b in allowed) for u in re.finditer(r'\b'+name+r'\b',body)):
            continue
        edits.append((decl.start(),decl.end(),'    u8 *'+name+';'))
        seed=seeds[0]
        edits.append((seed.start(1),seed.end(1),'(u8 *)'+seed[1]))
        changes.append({'local':name,'seed':seed[1],'kind':'closed-copy-byte-cursor',
            'authority':'closed source byte views plus target stride witnesses; register correspondence not proven'})
    for a,b,text in sorted(edits,reverse=True):
        a+=definition.end();b+=definition.end();source=source[:a]+text+source[b:]
    return source,changes


def lower(source,function, *, known_types=(), target_assembly=''):
    original_sha=hashlib.sha256(source.encode()).hexdigest()
    source,unaligned_stores=unaligned32_field_stores(source,function,target_assembly) if target_assembly else (source,[])
    source,record_reads=named_record_word_reads(source,function,target_assembly) if target_assembly else (source,[])
    source,address_externs=unknown_address_externs(source,function,target_assembly) if 'extern M2C_UNK' in source and target_assembly else (source,[])
    from solver import address_units
    source,indexed_reads=address_units.indexed_scalar_reads(source,function,target_assembly) if target_assembly else (source,[])
    source,unaligned=unaligned32_reads(source,function,target_assembly) if 'M2C_UNALIGNED32' in source and target_assembly else (source,[])
    source,word_temporaries=copied_word_temporaries(source,function,unaligned) if unaligned else (source,[])
    source,copy_cursors=closed_copy_cursors(source,function,target_assembly) if target_assembly else (source,[])
    masked=project_headers._mask_noncode(source)
    definition,end=repair_context.definition(source,function)
    start=definition.end()
    body=masked[start:end-1]
    if 'M2C_FIELD' in masked[:start]+masked[end:]:
        raise ValueError('field macros outside selected function')
    globals_=dict((m[1],m) for m in re.finditer(r'(?m)^extern s32 (\w+);',masked[:start]))
    # Scalar externs can coexist with reconstructed pointer roots. Only select
    # globals actually used in an address-bearing source shape; an unrelated
    # scalar read/arithmetic expression must not veto every local field.
    pointer_locals = re.findall(r'(?m)^\s*void\s*\*\s*(\w+)\s*;',body)
    roots = set()
    for name in globals_:
        if (any(re.search(r'\b'+re.escape(local)+r'\s*=\s*'+re.escape(name)+r'\s*\+(?![+=])',body)
                for local in pointer_locals)
                or re.search(r'\*\s*\(\s*'+re.escape(name)+r'\s*\+',body)
                or re.search(r'\bM2C_FIELD\s*\(\s*\(?\s*'+re.escape(name)+r'\s*\+',body)):
            roots.add(name)
    globals_={name:decl for name,decl in globals_.items() if name in roots}
    aliases={name:name for name in globals_}
    hypotheses=[]
    for local in pointer_locals:
        assignments=re.findall(r'\b'+re.escape(local)+r'\s*=\s*([^;]+);',body)
        roots=[]
        for expression in assignments:
            match=re.match(r'(\w+)\s*\+',expression)
            roots.append(match[1] if match and match[1] in globals_ else None)
        if roots and None not in roots and len(set(roots))==1:
            aliases[local]=roots[0]
    # Each root must be used only in byte-address additions inside this body.
    for name,decl in globals_.items():
        outside=masked[:decl.start()]+' '*(decl.end()-decl.start())+masked[decl.end():start]+masked[end:]
        if re.search(r'\b'+re.escape(name)+r'\b',outside):
            raise ValueError('global used outside selected function: '+name)
        for use in re.finditer(r'\b'+re.escape(name)+r'\b',body):
            if not re.match(r'\s*\+',body[use.end():]):
                raise ValueError('global has non-address use: '+name)
    fields=[]
    zero_types={}
    for match in re.finditer(r'\bM2C_FIELD\s*\(',body):
        stop=closing(body,match.end()-1)
        args=arguments(body[match.end():stop])
        spelling=re.fullmatch(r'(?:struct\s+)?(\w+)\s*(\*{1,2})',args[1]) if len(args)==3 else None
        callback=re.fullmatch(r'(.+?)\s*\(\s*\*\*\s*\)\s*\(([^()]*)\)',args[1]) if len(args)==3 else None
        if callback:
            allowed={'void','s8','u8','s16','u16','s32','u32','s64','u64','f32','f64',*known_types}
            types=[callback[1],*arguments(callback[2])]
            valid_types=all((m:=re.fullmatch(r'\s*(\w+)\s*\**\s*',t)) and m[1] in allowed for t in types)
            from solver import cfg,dataflow
            graph=cfg.build(target_assembly);sites=[]
            for i,ins in enumerate(graph.instructions):
                load=dataflow.MEMORY.fullmatch(', '.join(ins.operands))
                if ins.opcode!='lw' or not load or dataflow.number(load['offset'])!=dataflow.number(args[2]):continue
                reg=dataflow.reg(load['value'])
                if reg in {'sp','zero'}:continue
                for nxt in graph.instructions[i+1:i+13]:
                    if nxt.opcode=='jalr' and dataflow.reg(nxt.operands[-1])==reg:
                        sites.append(i);break
                    if nxt.opcode not in dataflow.WRITES_FIRST|{'sw','sh','sb','nop'}:break
                    if nxt.opcode in dataflow.WRITES_FIRST and dataflow.reg(nxt.operands[0])==reg:break
            if not valid_types or not sites:raise ValueError('unsupported/unwitnessed callback field: '+repr(args))
            fields.append({'base':args[0],'type':args[1],'offset':int(args[2],0),'root':None})
            hypotheses.append({'kind':'callback-pointer-field','base':args[0],'type':args[1],
                'target_load_instructions':sites,'scope':'load/call offset witness; inferred callback ABI remains unproven'})
            continue
        opaque_pointer=bool(spelling and spelling[1]=='M2C_UNK' and spelling[2]=='**')
        primitives={'s8','u8','s16','u16','s32','u32','s64','u64','f32','f64','float','double'}
        if not spelling or not (spelling[1] in primitives | set(known_types) or
                (spelling[1]=='void' and spelling[2]=='**') or opaque_pointer):
            raise ValueError('unsupported field macro arguments: '+repr(args)[:300])
        if spelling[1]=='void' and spelling[2]=='*':
            raise ValueError('unsized void field access')
        if not re.fullmatch(r'-?(?:0x[0-9a-fA-F]+|\d+)',args[2]):
            raise ValueError('nonliteral field offset')
        root=aliases.get(args[0])
        if opaque_pointer:
            hypotheses.append({'kind':'opaque-pointer-field-view','base':args[0],
                'offset':int(args[2],0),'before':args[1],'after':'void **',
                'scope':'pointer value only; pointee type and extent remain unknown'})
            args[1]='void **'
        fields.append({'base':args[0],'type':args[1],'offset':int(args[2],0),'root':root})
        if root and int(args[2],0)==0:
            zero_types.setdefault(root,set()).add(args[1])
    candidate=source
    # Bare dereferences are only admitted at a known root's indexed address.
    # The type comes from unanimous offset-zero field uses in the same root family.
    bare=[]
    for match in re.finditer(r'\*\s*\(\s*(\w+)\s*\+',body):
        root=match[1]
        if root not in globals_: continue
        types=zero_types.get(root,set())
        if len(types)!=1:
            raise ValueError('missing/ambiguous zero-offset type: '+root)
        paren=body.index('(',match.start())
        stop=closing(body,paren)
        bare.append((start+paren+1,'('+next(iter(types))+')('))
        bare.append((start+stop+1,')'))
        hypotheses.append({'kind':'bare-dereference-type','root':root,'type':next(iter(types)),
            'source_span':body[match.start():stop+1]})
    for at,text in sorted(bare,reverse=True):
        candidate=candidate[:at]+text+candidate[at:]
    # Expand inner macros first, so nested macro arguments stay balanced.
    while True:
        clean=project_headers._mask_noncode(candidate)
        matches=list(re.finditer(r'\bM2C_FIELD\s*\(',clean))
        if not matches: break
        match=matches[-1]; stop=closing(clean,match.end()-1)
        base,ctype,offset=arguments(candidate[match.end():stop])
        if re.fullmatch(r'M2C_UNK\s*\*\*',ctype):
            ctype='void **'
        candidate=candidate[:match.start()]+f'(*({ctype})((u8 *)({base}) + {offset}))'+candidate[stop+1:]
    for name in globals_:
        candidate=candidate.replace('extern s32 '+name+';','extern u8 *'+name+';',1)
        hypotheses.append({'kind':'byte-pointer-global-view','name':name})
    return {'source':candidate,'source_sha256':original_sha,'unaligned_reads':unaligned,'unaligned_stores':unaligned_stores,'named_record_reads':record_reads,'word_temporaries':word_temporaries,'copy_cursors':copy_cursors,'address_externs':address_externs,'indexed_scalar_reads':indexed_reads,
        'unaligned_assembly_sha256':hashlib.sha256(target_assembly.encode()).hexdigest() if unaligned or unaligned_stores or record_reads else None,
        'candidate_sha256':hashlib.sha256(candidate.encode()).hexdigest(),
        'fields':fields,'hypotheses':hypotheses,'aliases':aliases,
        'scope':'fresh-draft candidate lowering, not binary facts or a semantic certificate'}
