"""Named-global pseudo-field views bound to diagnosed sites and target accesses."""
import hashlib
import re
from pathlib import Path

from solver import dataflow, global_scalar_view, project_headers, repair_context, type_transaction

TYPES={'lb':'s8','lbu':'u8','lh':'s16','lhu':'u16','lw':'s32','lwc1':'f32',
       'sb':'s8','sh':'s16','sw':'s32','swc1':'f32'}


def propose(repo,source,function,diagnostics,assembly):
    report={'source':source,'changes':[],'declines':[],
        'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
        'scope':'named global address/offset/width hypothesis; no recovered member names or record layout'}
    if not any(text in diagnostics for text in ('member reference base type','no member named')):return report
    definition,end=repair_context.definition(source,function)
    mask=project_headers._mask_noncode(source);body=mask[definition.end():end-1]
    if re.search(r'(?m)^\s*#',body):return report
    headers=global_scalar_view.header_text(repo,source)
    macros=set(re.findall(r'(?m)^\s*#\s*define\s+(\w+)',project_headers._mask_comments(headers+'\n'+source)))
    known=set(type_transaction.typedef_names(headers+'\n'+source))|macros|{'signed','unsigned'}
    provided=project_headers._included_declarations(Path(repo),source)
    accesses={}
    for access in dataflow.analyse(assembly).accesses.values():
        address=access.address
        if address and address.kind=='address':
            accesses.setdefault((address.name,address.offset),[]).append(access)
    lines=source.splitlines(keepends=True);starts=[0]
    for line in lines:starts.append(starts[-1]+len(line))
    edits={}
    for diagnostic in re.finditer(r'^candidate\.c:(\d+):(\d+): error: (.*)$',diagnostics,re.M):
        number,column,message=int(diagnostic[1]),int(diagnostic[2]),diagnostic[3]
        missing=re.match(r"no member named '(unk[0-9A-Fa-f]+)' in '([^']+)'",message)
        scalar=re.match(r"member reference base type '([^']+)'(?: \(aka '[^']+'\))? is not a structure or union",message)
        if not (missing or scalar) or not 1<=number<=len(lines):continue
        line=lines[number-1].rstrip('\r\n');at=starts[number-1]
        excerpt=re.match(r'\n\s*'+str(number)+r' \| (.*)',diagnostics[diagnostic.end():])
        if not excerpt or excerpt[1]!=line or '\t' in line:continue
        for use in re.finditer(r'\b(\w+)\s*\.\s*(unk([0-9A-Fa-f]+))\b',mask[at:starts[number]]):
            if not use.start()<=column-1<use.end():continue
            symbol,member,offset=use[1],use[2],int(use[3],16)
            a,b=at+use.start(),at+use.end()
            if not definition.end()<=a<b<end-1:continue
            declarations=sorted(set(provided.get(symbol,[])))
            if len(declarations)!=1 or symbol in macros:continue
            declaration=re.fullmatch(r'extern\s+((?:(?:struct|union)\s+)?\w+)\s+'+re.escape(symbol)+r'\s*((?:\[\s*\w*\s*\])?)\s*;',declarations[0])
            if not declaration:continue
            spelling=declaration[1]+declaration[2]
            if missing and missing[1]!=member:continue
            if scalar:
                observed=re.sub(r'\s+','',scalar[1]); declared=re.sub(r'\s+','',spelling)
                if observed!=declared:
                    # Clang expands a header's enum/macro extent. The bound is
                    # not needed for a byte view; still require the same base
                    # type and exactly one array dimension at this fresh site.
                    if (not re.fullmatch(r'\[\s*[A-Za-z_]\w*\s*\]',declaration[2])
                            or not re.fullmatch(re.escape(re.sub(r'\s+','',declaration[1]))+r'\[\d+\]',observed)):
                        continue
            if re.search(r'\b'+re.escape(symbol)+r'\b',definition[2]):continue
            if global_scalar_view.shadowed(body,symbol,known|{declaration[1]}):continue
            before,after=mask[:a],mask[b:]
            if (before.rstrip().endswith(('.', '->'))
                    or re.search(r'\b(?:sizeof|_Alignof)\b[^;{}]*$',before)
                    or re.match(r'(?:\s*\))+\s*=(?!=)',after)
                    or re.search(r'(?:&|\+\+|--)\s*(?:\(\s*)*$',before)
                    or re.match(r'(?:\s*\))*\s*(?:\+\+|--|(?:<<|>>|[+\-*/%&|^])=|\[|\.|->)',after)):
                continue
            store=bool(re.match(r'\s*=(?!=)',after))
            peers=accesses.get((symbol,offset),[])
            if not peers or any(p.opcode not in TYPES for p in peers):continue
            if len({(p.width,TYPES[p.opcode]=='f32') for p in peers})!=1:continue
            selected=[p for p in peers if p.is_load!=store]
            spellings={TYPES[p.opcode] for p in selected}
            if len(spellings)!=1:continue
            typ=next(iter(spellings))
            replacement=f'(*({typ} *)((unsigned char *)&{symbol} + 0x{offset:X}))'
            edits[a,b]=(replacement,dict(symbol=symbol,offset=offset,type=typ,store=store,
                declaration=declarations[0],witnesses=[p.instruction for p in selected]))
    if len(edits)>128:return report
    for (a,b),(replacement,detail) in sorted(edits.items(),reverse=True):
        report['source']=report['source'][:a]+replacement+report['source'][b:]
    report['changes']=[dict(start=a,end=b,before=source[a:b],after=replacement,**detail)
        for (a,b),(replacement,detail) in sorted(edits.items())]
    return report
