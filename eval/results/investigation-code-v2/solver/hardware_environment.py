"""Project-header-backed register references that lack an admitted environment.

This detects unresolved register-macro relocations, not every hardware access.
It neither assigns guessed addresses nor models device behavior.
"""
import hashlib
import re


def encoded_register_addresses(assembly):
    """Recover relocation-name addresses from original instruction encodings.

    This is read-only address evidence, not device behavior or permission to
    replace registers with RAM. Reject rewritten/text-only or conflicting rows.
    """
    from solver import cfg, dataflow
    registers={}; rows={}; clear_after_delay=False
    names='zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 s0 s1 s2 s3 s4 s5 s6 s7 t8 t9 k0 k1 gp sp fp ra'.split()
    def number(operand):
        token=dataflow.reg(operand)
        if token=='s8':token='fp'
        return int(token) if token.isdigit() else names.index(token) if token in names else None
    for line in assembly.splitlines():
        record=re.match(r'^\s*/\*\s*[0-9A-Fa-f]+\s+[0-9A-Fa-f]+\s+([0-9A-Fa-f]{8})\s*\*/\s*(.*)$',line)
        if not record:
            if cfg.clean_line(line).endswith(':'):registers.clear()
            continue
        expire=clear_after_delay;clear_after_delay=False
        word=int(record[1],16); text=record[2]
        instructions,_=cfg.parse_assembly(text)
        if len(instructions)!=1:registers.clear();continue
        i=instructions[0]; op=word>>26; rt=(word>>16)&31; rs=(word>>21)&31
        symbol=re.search(r'%lo\((\w+_REG)\)',text)
        memory=re.search(r'\(\$?(\w+)\)\s*$',text)
        if symbol and memory and i.opcode in {'lw','sw'} and op==({'lw':35,'sw':43}[i.opcode]) and rs in registers and number(memory[1])==rs and number(i.operands[0])==rt:
            immediate=word&65535
            if immediate&32768:immediate-=65536
            address=(registers[rs]+immediate)&0xffffffff
            rows.setdefault(symbol[1],[]).append({'address':address,'width':4,'instruction_word':f'{word:08x}','text':text})
        if op==15 and i.opcode=='lui' and number(i.operands[0])==rt:registers[rt]=(word&65535)<<16
        elif dataflow._destination(i):
            # Register-number decoding covers the usual integer writes. Other
            # writers conservatively invalidate the complete tracked set.
            if op in {8,9,12,13,14,32,33,35,36,37}:registers.pop(rt,None)
            elif op==0 and (word&63) in {0,2,3,4,6,7,16,18,32,33,34,35,36,37,38,39,42,43} and number(i.operands[0])==((word>>11)&31):
                registers.pop((word>>11)&31,None)
            else:registers.clear()
        if expire:registers.clear()
        if cfg.is_control_transfer(i.opcode):clear_after_delay=True
    return {name:entries for name,entries in rows.items()
            if len({e['address'] for e in entries})==1}


def register_views(source,function,assembly):
    """Replace generated scalar register externs with encoded-address views.

    Does not admit a runtime hardware environment. Only closed scalar accesses
    in the N64 uncached peripheral region are candidates; addresses are never
    derived from physical-address header macros.
    """
    from solver import project_headers,repair_context
    report={'source':source,'changes':[],
            'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
            'authority':'encoded word-address volatile view candidate; device behavior unmodeled'}
    definition,end=repair_context.definition(source,function)
    mask=project_headers._mask_noncode(source);body=mask[definition.end():end-1]
    evidence=encoded_register_addresses(assembly);edits=[]
    for decl in re.finditer(r'(?m)^\s*extern\s+(s32|u32)\s+(\w+_REG)\s*;',mask[:definition.start()]):
        typ,name=decl.groups();rows=evidence.get(name,[])
        if not rows:continue
        address=rows[0]['address']
        if not 0xA4000000<=address<0xA5000000 or address%4:continue
        outside=mask[:decl.start()]+mask[decl.end():definition.end()]+mask[end:]
        if re.search(r'\b'+name+r'\b',outside):continue
        if re.search(r'(?m)^\s*\w+\s+\**\s*'+name+r'\s*[;=\[]',body):continue
        uses=list(re.finditer(r'\b'+name+r'\b',body))
        if not uses or any(re.search(r'(?:&|\.|->)\s*$',body[:m.start()]) or re.match(r'\s*[\[(.]',body[m.end():]) for m in uses):continue
        replacement=f'(*(volatile {typ} *)0x{address:08X}u)'
        edits.append((decl.start(),decl.end(),''))
        edits.extend((definition.end()+m.start(),definition.end()+m.end(),replacement) for m in uses)
        report['changes'].append({'symbol':name,'address':address,'type':typ,'encoded_witnesses':rows})
    for a,b,text in sorted(edits,reverse=True):source=source[:a]+text+source[b:]
    report['source']=source
    return report


class Required(ValueError):
    def __init__(self, evidence):
        self.evidence = evidence
        super().__init__('hardware register environment required: '+', '.join(
            row['symbol'] for row in evidence['register_references']))


def obligations(repo, assembly):
    header = repo/'include/PR/rcp.h'
    if not header.is_file():
        return None
    # Only explicit relocation memory operands, not symbol-name resemblance,
    # unused header macros, immediate constants or function names.
    referenced = set(re.findall(
        r'(?m)^\s*(?:lb|lbu|lh|lhu|lw|lwl|lwr|ld|sb|sh|sw|swl|swr|sd)\s+'
        r'[^,\n]+,\s*%lo\((\w+)\)\s*\(', assembly))
    rows = []
    text = header.read_text(errors='replace')
    for number, line in enumerate(text.splitlines(), 1):
        match = re.fullmatch(r'\s*#\s*define\s+(\w+_REG)\s+(.+)', line)
        if match and match[1] in referenced:
            rows.append({'symbol':match[1], 'line':number, 'macro':line.strip()})
    if not rows:
        return None
    return {'kind':'hardware-register-environment-required',
        'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
        'header':'include/PR/rcp.h', 'header_sha256':hashlib.sha256(header.read_bytes()).hexdigest(),
        'register_references':rows,
        'authority':'project RCP register header plus target relocation memory operands',
        'scope':'unresolved register references; not a complete hardware-access classifier',
        'next_action':'provide an independently validated device/callee-effect environment; do not seed registers as RAM'}
