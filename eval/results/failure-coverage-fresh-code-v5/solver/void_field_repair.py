"""Diagnostic-bound void-pointer pseudo-field candidates from binary widths.

Offset correspondence is a hypothesis, not proof that source and binary bases
denote the same object. No shared record declarations or extents are inferred.
"""
import hashlib
import re
from solver import dataflow, project_headers, repair_context


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
    flow=dataflow.analyse(assembly)
    for insn in flow.graph.instructions:
        if insn.opcode not in types or len(insn.operands)!=2:continue
        mem=re.fullmatch(r'(0x[0-9a-fA-F]+|[0-9]+)\(\$?(\w+)\)',insn.operands[1])
        if not mem or mem[2] in {'sp','fp','s8'}:continue
        access=flow.accesses.get(insn.index)
        address=access.address if access else None
        observations.setdefault(int(mem[1],0),[]).append({'instruction':insn.index,
            'opcode':insn.opcode,'type':types[insn.opcode],'width':widths[types[insn.opcode]],
            'root':address.name if address and address.kind=='address' else None,
            'root_offset':address.offset if address and address.kind=='address' else None})
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
            declarations=re.findall(r'(?m)^[ \t]*\w+\s+\**\s*'+re.escape(base)+r'\s*[;=]',body)
            if len(locals_)+len(parameters)!=1 or len(declarations)!=len(locals_):continue
            peers=observations.get(offset,[])
            if parameters:
                # A named pointer parameter has a stronger binding than a
                # raw displacement shared by unrelated loads elsewhere.
                word=definition[2].split(',').index(parameters[0])
                if word!=0:continue  # Later ABI words require parameter-width evidence.
                peers=[p for p in peers if p['root']=='param'+str(word) and p['root_offset']==offset]
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
