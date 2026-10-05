"""Diagnostic-bound scalar views of binary-observed global reads."""
import hashlib
import re
from pathlib import Path

from solver import dataflow, project_headers, repair_context, type_transaction


def header_text(repo, source):
    """Included headers only, constrained to the project's include tree."""
    root=(Path(repo)/'include').resolve()
    includes=re.compile(r'(?m)^\s*#\s*include\s*[<"]([^>"\n]+)[>"]')
    stack=[root/name for name in includes.findall(project_headers._mask_comments(source))]
    seen=set(); texts=[]
    while stack and len(seen)<400:
        path=stack.pop().resolve()
        if path in seen or not path.is_relative_to(root) or path.suffix!='.h' or not path.is_file():continue
        seen.add(path); text=path.read_text(errors='replace'); texts.append(text)
        for name in includes.findall(project_headers._mask_comments(text)):
            relative=path.parent/name
            stack.append(relative if relative.is_file() else root/name)
    return '\n'.join(texts)


def shadowed(body, name, known_types):
    if re.search(r'\b(?:struct|union|enum)\b[^;{}]*\{',body):return True
    controls={'return','goto','if','else','while','do','for','switch','case','break','continue'}
    for statement in re.split(r'[;{}]',body):
        tokens=re.findall(r'[A-Za-z_]\w*|\S',statement)
        if name not in tokens or len(tokens)<2 or tokens[0] in controls:continue
        if not re.fullmatch(r'[A-Za-z_]\w*',tokens[0]):continue
        if (re.fullmatch(r'[A-Za-z_]\w*',tokens[1]) or tokens[1]=='*'
                or (tokens[1]=='(' and (tokens[0] in known_types or
                    (len(tokens)>2 and tokens[2]=='*')))):return True
    return False


def propose(repo, source, function, diagnostics, assembly):
    report={'source':source,'changes':[],'declines':[],
        'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
        'scope':'diagnostic-bound global scalar-read hypothesis; width and signedness from target loads'}
    if 'invalid operands to binary expression' not in diagnostics:return report
    definition,end=repair_context.definition(source,function)
    mask=project_headers._mask_noncode(source);body=mask[definition.end():end-1]
    if re.search(r'(?m)^\s*#',body):return report
    provided=project_headers._included_declarations(Path(repo),source)
    headers=header_text(repo,source)
    known_types=set(type_transaction.typedef_names(headers+'\n'+source))
    known_types.update({'char','short','int','long','signed','unsigned','float','double'})
    macros=set(re.findall(r'(?m)^\s*#\s*define\s+(\w+)',project_headers._mask_comments(headers+'\n'+source)))
    types={'lb':'s8','lbu':'u8','lh':'s16','lhu':'u16','lw':'s32'}
    flow=dataflow.analyse(assembly); witnesses={}
    for access in flow.accesses.values():
        address=access.address
        if address and address.kind=='address' and address.offset==0 and access.is_load:
            witnesses.setdefault(address.name,[]).append(access)
    lines=source.splitlines(keepends=True);offsets=[0]
    for line in lines:offsets.append(offsets[-1]+len(line))
    edits={}
    pattern=re.compile(r'^candidate\.c:(\d+):(\d+): error: invalid operands to binary expression (.*)$',re.M)
    for diagnostic in pattern.finditer(diagnostics):
        number,column=int(diagnostic[1]),int(diagnostic[2])
        if not 1<=number<=len(lines):continue
        line=lines[number-1].rstrip('\r\n');at=offsets[number-1]
        excerpt=re.match(r'\n\s*'+str(number)+r' \| (.*)',diagnostics[diagnostic.end():])
        if not excerpt or excerpt[1]!=line or '\t' in line or not definition.end()<=at+column-1<end-1:continue
        operand_types=re.fullmatch(r"\('([^']+)'(?: \(aka '[^']+'\))? and '([^']+)'(?: \(aka '[^']+'\))?\)",diagnostic[3])
        operator_at=at+column-1
        operator=re.match(r'(?:<<|>>|<=|>=|==|!=|&&|\|\||[+*/%<>&|^\-])(?!=)',mask[operator_at:])
        if not operand_types or not operator:continue
        # Clang points at the faulty binary operator. Only repair its adjacent
        # bare operand with the corresponding reported type. Parenthesized or
        # complex operands decline; another use on this line is not authority.
        left=re.search(r'\b([A-Za-z_]\w*)\s*$',mask[at:operator_at])
        right_at=operator_at+operator.end()
        right=re.match(r'\s*([A-Za-z_]\w*)\b',mask[right_at:offsets[number]])
        operands=[]
        if left:operands.append((left[1],at+left.start(1),at+left.end(1),operand_types[1],False))
        if right:operands.append((right[1],right_at+right.start(1),right_at+right.end(1),operand_types[2],True))
        for name,a,b,operand_type,is_right in operands:
            decls=sorted(set(provided.get(name,[])))
            if len(decls)!=1 or name in macros or name not in witnesses:continue
            declaration=re.fullmatch(r'extern\s+((?:(?:struct|union)\s+)?\w+)\s+'+re.escape(name)+r'\s*;',decls[0])
            if not declaration or declaration[1]!=operand_type:continue
            if re.search(r'\b'+re.escape(name)+r'\b',definition[2]) or shadowed(body,name,known_types|macros|{declaration[1]}):continue
            before,after=mask[:a],mask[b:]
            if before.rstrip().endswith(('.', '->', ')', ']')) or re.match(r'\s*(?:\.|->|\[|\()',after):continue
            # Only reads; do not silently repair aggregate writes or updates.
            if ((not is_right and re.search(r'(?:&|\+\+|--)\s*(?:\(\s*)*$',before))
                    or re.match(r'(?:\s*\))*\s*(?:=(?!=)|(?:<<|>>|[+\-*/%&|^])=|\+\+|--)',after)):continue
            peers=witnesses[name];opcodes={p.opcode for p in peers}
            if len(opcodes)!=1 or not opcodes<=types.keys():continue
            opcode=next(iter(opcodes));typ=types[opcode]
            edits[a,b]=(f'(*({typ} *)&{name})',dict(symbol=name,type=typ,opcode=opcode,
                declaration=decls[0],witnesses=[p.instruction for p in peers]))
    if len(edits)>128:return report
    for (a,b),(replacement,detail) in sorted(edits.items(),reverse=True):
        report['source']=report['source'][:a]+replacement+report['source'][b:]
    report['changes']=[dict(start=a,end=b,before=source[a:b],after=replacement,**detail)
        for (a,b),(replacement,detail) in sorted(edits.items())]
    return report
