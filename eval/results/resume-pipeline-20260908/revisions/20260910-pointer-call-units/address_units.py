"""Source-bound address-unit candidates from resolved binary stores.

No pointee type or global extent is inferred. Existing source pointer views are
hypotheses; normal compiler and semantic gates retain authority.
"""
import hashlib
import re
from solver import dataflow, m2c_byte_view, project_headers, repair_context


def address_only_globals(source, function, assembly, symbols, provided=()):
    """Incomplete byte-array views for indexed objects or symbolic endpoints.

    Indexed uses require linker identity and closed address additions into
    byte-view locals. Comparison-only endpoints may retain an unknown numeric
    address. Both require a complete bounded same-block HI/LO pair without a
    clobber/call. No load width, field type or extent becomes a binary fact.
    """
    report = {'source': source, 'changes': [], 'declines': [],
        'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'assembly_sha256': hashlib.sha256(assembly.encode()).hexdigest()}
    definition, end = repair_context.definition(source, function)
    mask = project_headers._mask_noncode(source)
    body = mask[definition.end():end-1]
    flow = dataflow.analyse(assembly)
    declarations = list(re.finditer(r'(?m)^[ \t]*extern\s+(?:\?|M2C_UNK)\s+(\w+)\s*;', mask[:definition.start()]))
    edits = []
    for declaration in declarations:
        name = declaration[1]
        if name in provided or sum(d[1] == name for d in declarations) != 1:
            continue
        witnesses = []
        for i, insn in enumerate(flow.graph.instructions[:-1]):
            if insn.opcode != 'lui' or len(insn.operands) != 2 or insn.operands[1] != '%hi('+name+')':
                continue
            register=dataflow.reg(insn.operands[0])
            for j in range(i+1,min(i+17,len(flow.graph.instructions))):
                lo=flow.graph.instructions[j]
                if flow.graph.instruction_to_block[i] != flow.graph.instruction_to_block[j] or lo.opcode in {'jal','jalr','bal'}:
                    break
                if (lo.opcode == 'addiu' and len(lo.operands) == 3 and lo.operands[2] == '%lo('+name+')'
                        and dataflow.reg(lo.operands[1]) == register):
                    witnesses.append([i,j])
                    break
                if dataflow._destination(lo) == register:
                    break
        if not witnesses:
            continue
        outside = mask[:declaration.start()]+' '*(declaration.end()-declaration.start())+mask[declaration.end():definition.end()]+mask[end:]
        if re.search(r'\b'+name+r'\b', outside):
            continue
        uses = list(re.finditer(r'\b'+name+r'\b', body))
        accepted = []
        aliases = []
        stored_address_sites = []
        for i, access in flow.accesses.items():
            if access.is_load or access.opcode != 'sw' or i not in flow.instruction_in:
                continue
            instruction = flow.graph.instructions[i]
            value = flow.instruction_in[i].registers.get(dataflow.reg(instruction.operands[0]))
            if value == dataflow.Value.address(name):
                stored_address_sites.append(i)
        if stored_address_sites:
            # An explicit pointer-valued byte-view store needs only an address,
            # not the global's element type or extent. Keep the original symbol
            # and turn &unknown-object into the equivalent incomplete-array base.
            for store in re.finditer(r'\(\*\(void\s*\*\*\)\(\(u8\s*\*\)\(\w+\)\s*\+\s*(?:0x[0-9A-Fa-f]+|[0-9]+)\)\)\s*=\s*(&\s*'+re.escape(name)+r'\b)\s*;',body):
                accepted.append((store.start(1),store.end(1),name))
        comparison_sites = []
        unsigned_comparison_sites = []
        for insn in flow.graph.instructions:
            if insn.opcode == 'sltu' and len(insn.operands) == 3 and insn.index in flow.instruction_in:
                state = flow.instruction_in[insn.index]
                if state.registers.get(dataflow.reg(insn.operands[2])) == dataflow.Value.address(name):
                    unsigned_comparison_sites.append(insn.index)
            if insn.opcode not in {'beq','bne'} or insn.index not in flow.instruction_in:
                continue
            state=flow.instruction_in[insn.index]
            if any(state.registers.get(dataflow.reg(r)) == dataflow.Value.address(name) for r in insn.operands[:2]):
                comparison_sites.append(insn.index)
        if comparison_sites:
            for comparison in re.finditer(r'(?<![\w.>])\b(\w+)\s*(?:==|!=)\s*(&\s*'+name+r'\b)(?=\s*[),;])',body):
                local=comparison[1]
                if len(re.findall(r'(?m)^[ \t]*(?:struct\s+)?\w+\s*\*\s*'+local+r'\s*;',body)) != 1:
                    continue
                accepted.append((comparison.start(2),comparison.end(2),'(void *)'+name))
        if unsigned_comparison_sites:
            # Keep the explicit 32-bit unsigned address comparison. Replacing
            # this with C pointer ordering would add an unproved common-object
            # requirement; neither signed comparisons nor offsets are admitted.
            for comparison in re.finditer(r'\(u32\)\s*(\w+)\s*<\s*\(u32\)\s*(&\s*'+name+r'\b)(?=\s*[),;])', body):
                local = comparison[1]
                if len(re.findall(r'(?m)^[ \t]*(?:struct\s+)?\w+\s*\*\s*'+local+r'\s*;', body)) != 1:
                    continue
                accepted.append((comparison.start(2), comparison.end(2), name))
        for assignment in re.finditer(r'(?m)^[ \t]*(\w+)\s*=\s*([^;]+);', body):
            local = assignment[1]
            if not re.fullmatch(r'[^;=]+\+\s*&\s*'+name, assignment[2].strip()):
                continue
            if len(re.findall(r'(?m)^[ \t]*void\s*\*\s*'+local+r'\s*;', body)) != 1:
                continue
            if not re.search(r'\(\*\((?:s8|u8|s16|u16|s32|u32)\s*\*\)\(\(u8\s*\*\)\('+local+r'\)\s*\+', body):
                continue
            address = re.search(r'&\s*'+name+r'\b', assignment[2])
            start = assignment.start(2)+address.start()
            stop = assignment.start(2)+address.end()
            accepted.append((start,stop,name))
            aliases.append(local)
        inline_sites=[]
        for field in re.finditer(r'\(\*\((s8|u8|s16|u16|s32|u32)\s*\*\)\(\(u8\s*\*\)\(\((&\s*'+name+r')\s*\+\s*\(\w+->\w+\s*\*\s*(?:0x[0-9a-fA-F]+|\d+)\)\)\)\s*\+\s*(0x[0-9a-fA-F]+|\d+)\)\)\s*=(?!=)',body):
            width={'s8':1,'u8':1,'s16':2,'u16':2,'s32':4,'u32':4}[field[1]]
            offset=int(field[3],0);sites=[]
            for i,access in flow.accesses.items():
                if access.is_load or access.width!=width:continue
                ins=flow.graph.instructions[i];mem=dataflow.MEMORY.fullmatch(', '.join(ins.operands))
                if not mem or dataflow.number(mem['offset'])!=offset:continue
                base=dataflow.reg(mem['base'])
                for prior in reversed(flow.graph.instructions[max(0,i-20):i]):
                    if flow.graph.instruction_to_block[prior.index]!=flow.graph.instruction_to_block[i]:break
                    if dataflow._destination(prior)!=base:continue
                    if prior.opcode=='addu' and any(flow.instruction_in[prior.index].registers.get(dataflow.reg(r))==dataflow.Value.address(name) for r in prior.operands[1:]):sites.append(i)
                    break
            if sites and name in symbols:
                accepted.append((field.start(2),field.end(2),name))
                inline_sites.extend(sites)
        if not uses or len(accepted) != len(uses) or any(not any(a <= u.start() < b for a,b,_ in accepted) for u in uses):
            report['declines'].append({'global': name, 'reason': 'nonclosed indexed byte-address uses',
                'accepted_uses':len(accepted),'total_uses':len(uses),
                'unresolved_use_contexts':[body[max(0,u.start()-60):u.end()+80] for u in uses
                    if not any(a<=u.start()<b for a,b,_ in accepted)]})
            continue
        # An endpoint needs a named relocation/comparison, not an invented
        # integer address or object extent. Indexed byte views still require
        # the independent symbol-map identity used by their original guard.
        if name not in symbols and aliases:
            report['declines'].append({'global':name,'reason':'indexed view lacks independent symbol-map identity'})
            continue
        edits.append((declaration.start(),declaration.end(),'extern u8 '+name+'[];'))
        edits.extend((definition.end()+a,definition.end()+b,text) for a,b,text in accepted)
        report['changes'].append({'global': name, 'symbol_address': symbols.get(name),
            'identity_authority':'symbol map' if name in symbols else 'named target HI/LO and closed address use; numeric address unresolved',
            'address_pair_instructions': witnesses, 'byte_view_aliases': aliases,
            'stored_address_instructions':stored_address_sites,
            'comparison_instructions':comparison_sites,
            'unsigned_comparison_instructions':unsigned_comparison_sites,
            'inline_indexed_store_instructions':sorted(set(inline_sites)),
            'authority': 'candidate incomplete byte-array view; symbol address only, no field/extent claim'})
    for start, stop, replacement in sorted(edits, reverse=True):
        source = source[:start]+replacement+source[stop:]
    report.update(source=source,candidate_sha256=hashlib.sha256(source.encode()).hexdigest())
    return report


def indexed_scalar_reads(source, function, assembly):
    """Recover byte units in a shifted, relocated word-table read candidate."""
    from solver import cfg
    definition,end=repair_context.definition(source,function)
    mask=project_headers._mask_noncode(source);body=mask[definition.end():end-1]
    ins=cfg.build(assembly).instructions;edits=[];changes=[]
    for decl in re.finditer(r'(?m)^extern (s32|u32) (\w+);',mask[:definition.start()]):
        ctype,name=decl.groups()
        matches=list(re.finditer(r'\*\(&'+name+r'\s*\+\s*\((\w+->\w+)\s*\*\s*(\d+)\)\)',body))
        if len(matches)!=1 or len(re.findall(r'\b'+name+r'\b',mask))!=2:continue
        match=matches[0];factor=int(match[2]);witnesses=[]
        for i in range(len(ins)-3):
            hi,shift,add,read=ins[i:i+4]
            if [x.opcode for x in (hi,shift,add,read)]!=['lui','sll','addu','lw']:continue
            base=dataflow.reg(hi.operands[0]);scaled=dataflow.reg(shift.operands[0])
            amount=dataflow.number(shift.operands[2])
            load=dataflow.RELOC_MEMORY.fullmatch(', '.join(read.operands))
            if amount is None or not 0<=amount<32 or 1<<amount!=factor:continue
            if base in {'sp','zero'} or scaled in {base,'sp','zero'}:continue
            if hi.operands[1]!=f'%hi({name})' or not load or load['symbol']!=name or load['addend']:continue
            if dataflow.reg(add.operands[0])!=base or set(map(dataflow.reg,add.operands[1:]))!={base,scaled}:continue
            if dataflow.reg(load['base'])!=base:continue
            witnesses.append(i)
        if len(witnesses)!=1:continue
        replacement=f'(*({ctype} *)((u8 *)&{name} + ({match[1]} * {factor})))'
        edits.append((definition.end()+match.start(),definition.end()+match.end(),replacement))
        changes.append({'symbol':name,'byte_scale':factor,'target_instruction':witnesses[0],
            'scope':'target scale/load-width constrained candidate; source field identity not proven'})
    for a,b,text in sorted(edits,reverse=True):source=source[:a]+text+source[b:]
    return source,changes


def propose(source, function, assembly, *, absolute_symbols=None):
    report = {'source': source, 'changes': [], 'declines': [],
        'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'assembly_sha256': hashlib.sha256(assembly.encode()).hexdigest(),
        'authority': 'binary-store constrained source candidate; not inferred pointer types'}
    definition, end = repair_context.definition(source, function)
    mask = project_headers._mask_noncode(source)
    body = mask[definition.end():end-1]
    flow = dataflow.analyse(assembly)
    stores = []
    for i, access in flow.accesses.items():
        if access.is_load or access.opcode != 'sw':
            continue
        insn = flow.graph.instructions[i]
        value = flow.instruction_in[i].registers.get(dataflow.reg(insn.operands[0]))
        stores.append((i, access.address, value))
    edits = []
    globals_ = list(re.finditer(r'(?m)^[ \t]*extern\s+(s16|u16|s32|u32|f32|s64|u64|f64)\s*\*\s*(\w+)\s*;', mask[:definition.start()]))
    locals_ = list(re.finditer(r'(?m)^[ \t]*(s16|u16|s32|u32|f32|s64|u64|f64)\s*\*\s*(\w+)\s*;', body))
    for declaration in globals_:
        ctype, name = declaration.groups()
        if sum(d[2] == name for d in globals_) != 1:
            continue
        # Changing the local expressions must not rewrite other functions.
        assignments = list(re.finditer(r'\b'+name+r'\s*=(?!=)\s*([^;]+);', body))
        if not assignments:
            continue
        if re.search(r'\b'+name+r'\s*(?:[+\-*/%&|^]=|<<=|>>=|\+\+|--)|(?:\+\+|--|&)\s*\b'+name+r'\b', body):
            report['declines'].append({'global': name, 'reason': 'global address escapes or unsupported write syntax'})
            continue
        plans = []
        witnesses = []
        failed = False
        binary = [(i, v) for i, a, v in stores if a == dataflow.Value.address(name)]
        for assignment in assignments:
            expr = re.fullmatch(r'(\w+)\s*\+\s*(0x[0-9A-Fa-f]+|[1-9][0-9]*)', assignment[1].strip())
            prefix = body[:assignment.start()].rstrip()
            if not expr or (prefix and prefix[-1] not in ';{}:'):
                failed = True
                break
            local, literal = expr.groups()
            step = int(literal, 0)
            decls = [d for d in locals_ if d[2] == local]
            writes = list(re.finditer(r'\b'+local+r'\s*=(?!=)\s*([^;]+);', body))
            modifications = re.search(r'\b'+local+r'\s*(?:[+\-*/%&|^]=|<<=|>>=|\+\+|--)|(?:\+\+|--|&)\s*\b'+local+r'\b', body)
            if (len(decls) != 1 or decls[0][1] != ctype or len(writes) != 1
                    or writes[0][1].strip() != name or writes[0].start() >= assignment.start() or modifications):
                failed = True
                break
            peers = [i for i, v in binary if v is not None and v.kind == 'load'
                and v.inner == dataflow.Value.address(name) and v.width == 4 and v.offset == step]
            if not peers:
                failed = True
                break
            plans.append((assignment.start(), assignment.end(),
                f'{name} = ({ctype} *)((u8 *){local} + {literal});'))
            witnesses.extend(peers)
        # Entire write family must agree, including paths hidden behind calls.
        if failed or len(binary) != len(plans) or set(witnesses) != {i for i, _ in binary}:
            report['declines'].append({'global': name, 'reason': 'unclosed source writes or unresolved/mismatched target update family'})
            continue
        edits.extend(plans)
        report['changes'].append({'kind': 'global-pointer-byte-steps', 'global': name,
            'source_updates': len(plans), 'target_store_instructions': sorted(set(witnesses))})
    # Restore address typing lost by absolute-symbol substitution, only at an
    # explicit void-pointer byte-store lvalue and an observed target constant.
    for match in re.finditer(r'\(\*\(void\s*\*\*\)\(\(u8\s*\*\)\(', body):
        stop = m2c_byte_view.closing(body, match.start())
        rhs = re.match(r'\s*=\s*(0x[0-9A-Fa-f]+)\s*;', body[stop+1:])
        if not rhs:
            continue
        value = int(rhs[1], 16)
        symbols = [n for n, v in (absolute_symbols or {}).items() if v == value]
        sites = [i for i, _, v in stores if v == dataflow.Value.constant(value)
            or (v is not None and v.kind == 'address' and v.name in symbols and v.offset == 0)]
        if not symbols or not sites:
            report['declines'].append({'literal': rhs[1], 'reason': 'missing linker identity or target constant store'})
            continue
        begin = stop+1+rhs.start(1)
        edits.append((begin, stop+1+rhs.end(1), '(void *)'+rhs[1]))
        report['changes'].append({'kind': 'absolute-address-pointer-store', 'symbols': sorted(symbols),
            'value': value, 'target_store_instructions': sites})
    candidate = source
    for start, stop, text in sorted(edits, reverse=True):
        candidate = candidate[:definition.end()+start]+text+candidate[definition.end()+stop:]
    candidate,indexed=indexed_scalar_reads(candidate,function,assembly)
    report['changes'].extend({'kind':'indexed-scalar-byte-units',**row} for row in indexed)
    report['source'] = candidate
    report['candidate_sha256'] = hashlib.sha256(candidate.encode()).hexdigest()
    return report
