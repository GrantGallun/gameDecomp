"""Header-array call contracts plus target stack slots reconstruct storage candidates."""
import hashlib
import re
from pathlib import Path

from solver import cfg, dataflow, project_headers, repair_context, m2c_byte_view


def header_arrays(repo, source):
    root = (Path(repo)/'include').resolve()
    include = re.compile(r'(?m)^\s*#\s*include\s*[<"]([^>"\n]+)[>"]')
    pending = [root/p for p in include.findall(source)]; seen = set(); arrays = {}; receipts = []
    while pending and len(seen) < 400:
        path = pending.pop().resolve()
        if path in seen or not path.is_relative_to(root) or path.suffix != '.h' or not path.is_file():
            continue
        seen.add(path); text = path.read_text(errors='replace')
        mask = project_headers._mask_comments(text)
        for m in re.finditer(r'\btypedef\s+(s16|u16|s32|u32)\s+(\w+)\s*\[\s*(\d+)\s*\]\s*;', mask):
            arrays.setdefault(m[2], set()).add((m[1], int(m[3])))
            receipts.append({'path': str(path), 'sha256': hashlib.sha256(text.encode()).hexdigest(), 'declaration': m[0]})
        for inc in include.findall(mask):
            relative = path.parent/inc
            pending.append(relative if relative.is_file() else root/inc)
    return {k: next(iter(v)) for k,v in arrays.items() if len(v) == 1}, receipts


def propose(source, function, assembly, arrays, declarations):
    report = {'source': source, 'changes': [], 'declines': [],
        'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'assembly_sha256': hashlib.sha256(assembly.encode()).hexdigest(),
        'authority': 'syntactic header contract and target stack accesses; allocation/lifetime correspondence remains a candidate hypothesis'}
    definition, end = repair_context.definition(source, function)
    body = project_headers._mask_noncode(source)[definition.end():end-1]
    flow = dataflow.analyse(assembly)
    frames = {s.registers['sp'].offset for s in flow.instruction_in.values()
              if s.registers.get('sp') and s.registers['sp'].kind == 'address'
              and s.registers['sp'].name == 'stack' and s.registers['sp'].offset < 0}
    if len(frames) != 1:
        return report
    frame = next(iter(frames)); edits = []
    instructions, _ = cfg.parse_assembly(assembly)
    address_offsets = set()
    for ins in instructions:
        if ins.opcode == 'addiu' and len(ins.operands) == 3 and dataflow.reg(ins.operands[1]) == 'sp':
            try:
                address_offsets.add(int(ins.operands[2], 0))
            except ValueError:
                pass
    calls = []
    for call in re.finditer(r'\b(\w+)\s*\(', body):
        stop = m2c_byte_view.closing(body, call.end()-1)
        args = m2c_byte_view.arguments(body[call.end():stop]); at = call.end()
        protos = set(declarations.get(call[1], []))
        if len(protos) != 1:
            continue
        proto = next(iter(protos)); pm = re.search(r'\b'+call[1]+r'\s*\(([^()]*)\)', proto)
        if not pm:
            continue
        params = pm[1].split(',')
        for slot, arg in enumerate(args):
            pos = body.find(arg, at, stop); at = pos+len(arg)
            ptr = re.fullmatch(r'&\s*(sp[0-9A-Fa-f]+)', arg)
            if not ptr or slot >= min(4,len(params)):
                continue
            words = params[slot].strip().split()
            if not words:
                continue
            typ = words[0]
            if typ in arrays and re.fullmatch(re.escape(typ)+r'(?:\s+\w+)?', params[slot].strip()):
                calls.append((ptr[1], typ, call[1], slot, pos, at))
    roots = sorted({c[0] for c in calls})
    for root in roots:
        selected = [c for c in calls if c[0] == root]
        types = {c[1] for c in selected}
        if len(types) != 1:
            continue
        typ = next(iter(types)); element, count = arrays[typ]
        pointer_names = set(re.findall(r'(?m)^\s*'+element+r'\s*\*\s*(\w+)\s*;', body))
        width = 2 if element.endswith('16') else 4
        offset = int(root[2:],16); size = width*count
        if any(other != root and offset <= int(other[2:],16) < offset+size for other in roots):
            continue
        decls = list(re.finditer(r'(?m)^[ \t]*'+element+r'\s+'+root+r'\s*;', body))
        if len(decls) != 1 or not 2 <= count <= 64 or offset < 16 or offset+size > -frame:
            continue
        witnesses = []; used = set(); failed = False
        for _, _, callee, slot, _, _ in selected:
            matches = [c for c in flow.callsites.values() if c.target == callee and
                       c.arguments[slot] == dataflow.Value.address('stack', frame+offset)
                       and c.instruction not in used]
            if not matches:
                failed = True; break
            used.add(matches[0].instruction); witnesses.append(matches[0].instruction)
        if failed:
            continue
        local_edits = [(decls[0].start(), decls[0].end(), f'    {typ} {root};')]
        local_edits.extend((a,b,root) for _,_,_,_,a,b in selected)
        aliases = {m[0] for m in re.finditer(r'\bsp[0-9A-Fa-f]+\b',body)
                   if offset <= int(m[0][2:],16) < offset+size}
        for alias in aliases:
            relative = int(alias[2:],16)-offset
            ds = list(re.finditer(r'(?m)^[ \t]*'+element+r'\s+'+alias+r'\s*;',body))
            all_ds = list(re.finditer(r'(?m)^[ \t]*\w+\s+\**\s*'+alias+r'\s*(?:;|\[)',body))
            if relative % width or len(ds)>1 or len(ds)!=len(all_ds):
                failed=True; break
            uses = [m for m in re.finditer(r'\b'+alias+r'\b',body)
                    if not any(d.start()<=m.start()<d.end() for d in ds)
                    and not any(a<=m.start()<b for _,_,_,_,a,b in selected)]
            access = [a for a in flow.accesses.values() if a.address ==
                      dataflow.Value.address('stack',frame+offset+relative) and a.width==width
                      and a.opcode in {'lh','lhu','sh','lw','sw'}]
            invalid_use = False
            for use in uses:
                if re.match(r'\s*[\[.]', body[use.end():]):
                    invalid_use = True
                if re.search(r'&\s*$', body[:use.start()]):
                    # Only a local, same-element pointer seed or endpoint. The
                    # address stays inside this array; do not allow call escapes
                    # or assignment to an undeclared/global destination.
                    prefix = body[:use.start()]
                    seed = re.search(r'(?:^|[;{}\n])\s*(\w+)\s*=\s*&\s*$', prefix)
                    endpoint = re.search(r'\b(\w+)\s*(?:!=|==)\s*&\s*$', prefix)
                    match = seed or endpoint
                    if (not match or match[1] not in pointer_names or
                            offset+relative not in address_offsets):
                        invalid_use = True
                elif not access:
                    invalid_use = True
            if invalid_use:
                failed=True; break
            if alias != root:
                local_edits.extend((d.start(),d.end(),'') for d in ds)
            local_edits.extend((m.start(),m.end(),f'{root}[{relative//width}]') for m in uses)
        if failed:
            continue
        edits.extend(local_edits)
        report['changes'].append({'root':root,'type':typ,'element':element,'count':count,
                                  'call_instructions':witnesses,'aliases':sorted(aliases)})
    for a,b,text in sorted(edits,reverse=True):
        source=source[:definition.end()+a]+text+source[definition.end()+b:]
    report['source']=source
    return report


def recover(repo, source, function, assembly):
    arrays, receipts = header_arrays(repo, source)
    report = propose(source,function,assembly,arrays,project_headers._included_declarations(repo,source))
    report['header_evidence'] = receipts
    return report
