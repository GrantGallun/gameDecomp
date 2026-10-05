"""Diagnostic-bound void-pointer pseudo-field candidates from binary widths.

Offset correspondence is a hypothesis, not proof that source and binary bases
denote the same object. No shared record declarations or extents are inferred.
"""
import hashlib
import re
from solver import dataflow, project_headers, repair_context


def _possible_local_declaration(body, name):
    """Conservatively recognize a name in a declaration-shaped statement.

    No type table is available here, so unknown type names and complex
    declarators must decline too. False positives only withhold a hypothesis.
    """
    # Splitting statements would discard the declarator after a braced local
    # aggregate. Such bodies are outside this deliberately narrow alias rule.
    if re.search(r'\b(?:struct|union|enum)\b[^;{}]*\{',body):
        return True
    controls={'return','goto','if','else','while','do','switch','case','break','continue'}
    for statement in re.split(r'[;{}]',body):
        statement=re.sub(r'^\s*(?:[A-Za-z_]\w*\s*:\s*)+','',statement)
        tokens=re.findall(r'[A-Za-z_]\w*|\S',statement)
        if name not in tokens or len(tokens)<2:continue
        if tokens[0] in controls or not re.fullmatch(r'[A-Za-z_]\w*',tokens[0]):continue
        if tokens[1] in {'*','('} or re.fullmatch(r'[A-Za-z_]\w*',tokens[1]):
            return True
    return False


def propose(source, function, assembly, diagnostics):
    report={'source':source,'changes':[],
            'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
            'scope':'offset/width candidate correspondence; not base identity or semantic proof'}
    if "member reference base type 'void' is not a structure or union" not in diagnostics:
        return report
    definition,end=repair_context.definition(source,function)
    mask=project_headers._mask_noncode(source)
    body=mask[definition.end():end-1]
    observations={}
    types={'lb':'s8','lbu':'u8','lh':'s16','lhu':'u16','lw':'s32',
           'sb':'s8','sh':'s16','sw':'s32','lwc1':'f32','swc1':'f32'}
    widths={'s8':1,'u8':1,'s16':2,'u16':2,'s32':4,'f32':4}
    scalar=r'(?:s8|u8|s16|u16|s32|u32|int|unsigned\s+int|signed\s+int)\s+\w+'
    pointer=r'(?:(?:const|volatile)\s+)*(?:struct\s+)?\w+\s*\*\s*\w+'
    parameters_=definition[2].split(',')
    known_words=0
    for parameter in parameters_[:8]:
        if not re.fullmatch(r'\s*(?:'+scalar+'|'+pointer+r')\s*',parameter):
            break
        known_words+=1
    flow=dataflow.analyse(assembly,stack_parameter_words=max(0,known_words-4))
    # A copied global pointer has a stronger binary identity than an arbitrary
    # local at the same displacement. Keep this separate from parameter aliases:
    # load(gCursor)+4 is not the address of gCursor+4.
    global_pointers={}
    for access in flow.accesses.values():
        address=access.address
        if (address and address.kind=='load' and address.width==4 and address.inner
                and address.inner.kind=='address' and address.inner.offset==0
                and address.inner.name not in {'stack','gp'}
                and not re.fullmatch(r'param\d+',address.inner.name)):
            global_pointers.setdefault(address.inner.name,[]).append(address)
    global_aliases={}
    for name in re.findall(r'(?m)^[ \t]*void\s*\*\s*(\w+)\s*;',body):
        assignments=list(re.finditer(r'\b'+re.escape(name)+r'\s*=(?!=)\s*([^;]+);',body))
        if len(assignments)!=1:continue
        assignment=assignments[0]
        symbol=assignment[1].strip()
        if symbol not in global_pointers:continue
        # Only a plain statement assigning the symbol, with no address escape
        # or subsequent cursor mutation. Other local alias forms still decline.
        line_start=body.rfind('\n',0,assignment.start())+1
        if body[line_start:assignment.start()].strip():continue
        if re.search(r'\b'+re.escape(name)+r'\s*(?:[+\-*/%&|^]=|<<=|>>=|\+\+|--)',body):continue
        if re.search(r'(?:\+\+|--|&)\s*'+re.escape(name)+r'\b',body):continue
        # Parenthesized lvalues can hide assignment, increment or & from the
        # simple guards above. This narrow alias rule does not interpret them.
        if re.search(r'\(\s*'+re.escape(name)+r'\s*\)',body):continue
        if re.search(r'\b'+re.escape(symbol)+r'\b',definition[2]):continue
        if _possible_local_declaration(body,symbol):continue
        remaining=re.sub(r'(?m)^[ \t]*void\s*\*\s*'+re.escape(name)+r'\s*;','',body)
        if _possible_local_declaration(remaining,name):continue
        global_aliases[name]=(symbol,assignment.end())
    # Closed single-assignment byte-address chains keep parameter identity even
    # when the target folds a cursor's offset into a zero-displacement store.
    aliases={p.strip().split()[-1].lstrip('*'):(f'param{i}',0,0)
             for i,p in enumerate(parameters_[:known_words]) if '*' in p}
    local_names=re.findall(r'(?m)^[ \t]*void\s*\*\s*(\w+)\s*;',body)
    for _ in range(8):
        changed=False
        for name in local_names:
            if name in aliases or local_names.count(name)!=1:continue
            assignments=list(re.finditer(r'\b'+name+r'\s*=(?!=)\s*([^;]+);',body))
            if len(assignments)!=1 or re.search(r'\b'+name+r'\s*(?:\+=|-=|\+\+|--)',body):continue
            expression=assignments[0][1]
            if not re.search(r'\(\s*(?:unsigned\s+char|u8)\s*\*\s*\)',expression):continue
            simple=re.sub(r'\(\s*(?:void|unsigned\s+char|u8)\s*\*\s*\)','',expression)
            simple=re.sub(r'[\s()]','',simple)
            match=re.fullmatch(r'(\w+)\+(0x[\da-fA-F]+|\d+)',simple)
            if not match or match[1] not in aliases:continue
            root,base_offset,at=aliases[match[1]]
            if at>assignments[0].start():continue
            aliases[name]=(root,base_offset+int(match[2],0),assignments[0].end())
            changed=True
        if not changed:break
    for insn in flow.graph.instructions:
        if insn.opcode not in types or len(insn.operands)!=2:continue
        mem=re.fullmatch(r'(0x[0-9a-fA-F]+|[0-9]+)\(\$?(\w+)\)',insn.operands[1])
        if not mem or mem[2] in {'sp','fp','s8'}:continue
        access=flow.accesses.get(insn.index)
        address=access.address if access else None
        observations.setdefault(int(mem[1],0),[]).append({'instruction':insn.index,
            'opcode':insn.opcode,'type':types[insn.opcode],'width':widths[types[insn.opcode]],
            'root':address.name if address and address.kind=='address' else None,
            'root_offset':address.offset if address and address.kind=='address' else None,
            'global_pointer':address.inner.name if address and address.kind=='load'
                and address.width==4 and address.inner and address.inner.kind=='address'
                and address.inner.offset==0 else None,
            'global_pointer_offset':address.offset if address and address.kind=='load' else None})
    lines=source.splitlines(keepends=True); offsets=[0]
    for line in lines:offsets.append(offsets[-1]+len(line))
    edits={}
    pattern=r"(?m)^candidate\.c:(\d+):(\d+): error: member reference base type 'void' is not a structure or union"
    for diagnostic in re.finditer(pattern,diagnostics):
        number,column=int(diagnostic[1]),int(diagnostic[2])
        if not 1<=number<=len(lines):continue
        line=lines[number-1].rstrip('\r\n')
        excerpt=re.match(r'\n\s*'+str(number)+r' \| (.*)',diagnostics[diagnostic.end():])
        if not excerpt or excerpt[1]!=line or '\t' in line:continue
        for use in re.finditer(r'\b(\w+)->unk([0-9a-fA-F]+)\b',project_headers._mask_noncode(line)):
            if not use.start()<=column-1<use.end():continue
            base,hexoffset=use.groups();offset=int(hexoffset,16)
            locals_=re.findall(r'(?m)^[ \t]*void\s*\*\s*'+re.escape(base)+r'\s*;',body)
            parameters=[p for p in definition[2].split(',') if re.fullmatch(r'\s*void\s*\*\s*'+re.escape(base)+r'\s*',p)]
            # A returned local is an expression, not a second declaration.
            declarations=re.findall(r'(?m)^[ \t]*(?!return\b)\w+\s+\**\s*'+re.escape(base)+r'\s*[;=]',body)
            if len(locals_)+len(parameters)!=1 or len(declarations)!=len(locals_):continue
            peers=observations.get(offset,[])
            if locals_ and base in global_aliases:
                symbol,at=global_aliases[base]
                if offsets[number-1]+use.start()-definition.end()<at:continue
                peers=[p for group in observations.values() for p in group
                       if p['global_pointer']==symbol and p['global_pointer_offset']==offset]
            if locals_ and base in aliases:
                root,base_offset,at=aliases[base]
                if offsets[number-1]+use.start()-definition.end()<at:continue
                peers=[p for group in observations.values() for p in group
                       if p['root']==root and p['root_offset']==base_offset+offset]
            if parameters:
                # A named pointer parameter has a stronger binding than a
                # raw displacement shared by unrelated loads elsewhere.
                word=definition[2].split(',').index(parameters[0])
                # Later argument registers are usable only when every preceding
                # declaration is unambiguously one integer/pointer ABI word.
                # Wide, floating, aggregate and unknown typedef parameters decline.
                prefix=definition[2].split(',')[:word]
                scalar=r'(?:s8|u8|s16|u16|s32|u32|int|unsigned\s+int|signed\s+int)\s+\w+'
                pointer=r'(?:(?:const|volatile)\s+)*(?:struct\s+)?\w+\s*\*\s*\w+'
                if word>7 or any(not re.fullmatch(r'\s*(?:'+scalar+'|'+pointer+r')\s*',p) for p in prefix):
                    continue
                # Parameter-relative displacement may be split between an
                # address calculation and the final memory instruction.
                peers=[p for group in observations.values() for p in group
                       if p['root']=='param'+str(word) and p['root_offset']==offset]
            if not peers or len({(p['width'],p['type']=='f32') for p in peers})!=1:continue
            is_store=bool(re.match(r'\s*=(?!=)',line[use.end():]))
            matching=[p for p in peers if p['opcode'].startswith('s' if is_store else 'l')]
            spellings={p['type'] for p in matching}
            if len(spellings)!=1:continue
            typ=next(iter(spellings))
            a,b=offsets[number-1]+use.start(),offsets[number-1]+use.end()
            if not definition.end()<=a<b<end:continue
            replacement=f'(*({typ} *)((unsigned char *){base} + 0x{offset:X}))'
            edits[a,b]=replacement
            report['changes'].append({'before':source[a:b],'after':replacement,
                'offset':offset,'type':typ,'witnesses':matching})
    if len(edits)>128:return {**report,'changes':[]}
    for (a,b),replacement in sorted(edits.items(),reverse=True):
        report['source']=report['source'][:a]+replacement+report['source'][b:]
    return report
