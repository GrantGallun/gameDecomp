"""Bounded indexed stack-word storage candidates, not original-layout proofs."""
import hashlib
import re

from solver import cfg, dataflow, project_headers, repair_context


def store_witnesses(assembly):
    graph = cfg.build(assembly)
    ins = graph.instructions
    rows = []
    for shift in ins:
        a = tuple(dataflow.reg(x) for x in shift.operands)
        if shift.opcode != 'sll' or len(a) != 3 or dataflow.number(a[2]) != 2:
            continue
        regs = {a[0]: ('index', 0)}
        stores = []
        bound = None
        for i in ins[shift.index+1:shift.index+49]:
            args = tuple(dataflow.reg(x) for x in i.operands)
            if i.opcode in cfg.CALL_OPS:
                break
            if i.opcode == 'slti' and len(args) == 3 and args[1] == a[1]:
                bound = (args[0], dataflow.number(args[2]))
            if i.opcode == 'bnez' and bound and args[0] == bound[0]:
                start = graph.label_to_instruction.get(i.operands[1], shift.index+1)
                if start <= shift.index and bound[1] and 2 <= bound[1] <= 64:
                    rows.extend(dict(offset=o, count=bound[1], instruction=n,
                                     shift=shift.index, branch=i.index) for o, n in stores)
                break
            value = None
            if i.opcode == 'addiu' and len(args) == 3 and args[1] == 'sp':
                amount = dataflow.number(args[2])
                if amount is not None:
                    value = ('stack-base', amount)
            if i.opcode == 'addu' and len(args) == 3:
                for x, y in ((args[1], args[2]), (args[2], args[1])):
                    if x == 'sp' and regs.get(y) == ('index', 0):
                        value = ('stack-index', 0)
                    if (regs.get(x) or (None,))[0] == 'stack-base' and regs.get(y) == ('index', 0):
                        value = ('stack-index', regs[x][1])
            if i.opcode == 'sw' and len(args) == 2:
                mem = re.fullmatch(r'(-?(?:0x[0-9a-fA-F]+|\d+))\((\w+)\)', args[1].replace('$', ''))
                if mem and (regs.get(mem[2]) or (None,))[0] == 'stack-index':
                    stores.append((int(mem[1], 0)+regs[mem[2]][1], i.index))
            dest = dataflow._destination(i)
            if dest:
                regs[dataflow.reg(dest)] = value
    return rows


def propose(source, function, assembly):
    report = {'source': source, 'changes': [], 'declines': [],
              'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
              'assembly_sha256': hashlib.sha256(assembly.encode()).hexdigest(),
              'authority': 'bounded stack-word candidate; source/target index correspondence and missing alias signedness are hypotheses'}
    definition, end = repair_context.definition(source, function)
    mask = project_headers._mask_noncode(source)
    body = mask[definition.end():end-1]
    binary = store_witnesses(assembly)
    flow = dataflow.analyse(assembly)
    instructions = tuple(i for i in cfg.build(assembly).instructions
                         if i.opcode not in {'nonmatching', 'endlabel'})
    if not instructions or instructions[0].opcode != 'addiu':
        return report
    first = tuple(dataflow.reg(x) for x in instructions[0].operands)
    if first[:2] != ('sp', 'sp'):
        return report
    frame = dataflow.number(first[2])
    if frame is None or not -4096 <= frame < 0:
        return report
    for i in instructions[1:]:
        if dataflow.reg(dataflow._destination(i) or '') == 'sp':
            a = tuple(dataflow.reg(x) for x in i.operands)
            if i.opcode != 'addiu' or a[:2] != ('sp', 'sp') or dataflow.number(a[2]) != -frame:
                return report
    edits = []
    spans = []
    # Raw indexed stores and first-element address anchors share the same
    # storage reconstruction. A named anchor still needs a target indexed store.
    pattern = r'\(\*\(s32 \*\)\(\(u8 \*\)\(\(sp \+ (\w+)\)\) \+ (0x[0-9A-Fa-f]+)\)\)'
    accesses = [(m, m[1], int(m[2],16), None) for m in re.finditer(pattern, body)]
    anchor_pattern = r'&\(&(?P<root>sp[0-9A-Fa-f]+)\[0\]\)\[(?P<iv>\w+)\]'
    for m in re.finditer(anchor_pattern, body):
        seeds = list(re.finditer(r'\b(\w+)\s*=\s*'+m['iv']+r'\s*\*\s*4\s*;', body[:m.start()]))
        if len(seeds) == 1:
            accesses.append((m, seeds[0][1], int(m['root'][2:],16), m['root']))
    for access, index, offset, anchor in accesses:
        prefix = body[:access.start()]
        seeds = list(re.finditer(r'\b'+index+r'\s*=\s*(\w+)\s*\*\s*4\s*;', prefix))
        if len(seeds) != 1:
            continue
        seed = seeds[0]; iv = seed[1]
        loops = list(re.finditer(r'\b'+iv+r'\s*=\s*0\s*;\s*for\s*\(\s*;\s*;\s*\)\s*\{', prefix))
        if not loops:
            continue
        loop = loops[-1]
        depth = 1; stop = loop.end()
        while stop < len(body) and depth:
            depth += (body[stop] == '{') - (body[stop] == '}')
            stop += 1
        stop -= 1
        tail = body[access.end():stop]
        limit = re.search(r'\b'+iv+r'\s*\+=\s*1\s*;\s*if\s*\(!\('+iv+r'\s*<\s*(\d+)\)\)\s*break\s*;\s*$', tail)
        if not limit or not loop.end() <= seed.start() < access.start():
            continue
        count = int(limit[1])
        if offset < 16 or offset+4*count > -frame:
            continue
        witnesses = [w for w in binary if w['offset'] == offset and w['count'] == count]
        witness_loops = {(w['shift'],w['branch']) for w in witnesses}
        if len(witness_loops) != 1 or any(max(offset, lo) < min(offset+4*count, hi) for lo, hi in spans):
            continue
        region = body[loop.end():stop]
        if len(re.findall(r'\b'+iv+r'\s*(?:=(?!=)|\+=|-=|\+\+|--)', region)) != 1:
            continue
        if re.search(r'\b'+index+r'\s*(?:=(?!=)|\+=|-=|\+\+|--)', body[seed.end():access.start()]):
            continue
        name = 'stack_words_'+format(offset, 'X')
        if re.search(r'\b'+name+r'\b', mask):
            continue
        aliases = {m[0] for m in re.finditer(r'\bsp[0-9A-Fa-f]+\b', body)
                   if offset <= int(m[0][2:], 16) < offset+4*count}
        changes = []; failed = False; alias_rows = []
        for alias in sorted(aliases):
            off = int(alias[2:], 16)-offset
            decls = list(re.finditer(r'(?m)^[ \t]*(s32|u32)\s+'+alias+r'\s*;', body))
            other_decls = list(re.finditer(r'(?m)^[ \t]*\w+\s+\**\s*'+alias+r'\s*(?:;|\[)', body))
            if len(other_decls) != len(decls):
                failed = True; break
            if off % 4 or len(decls) > 1:
                failed = True; break
            typ = decls[0][1] if decls else 's32'
            if alias == anchor:
                uses = list(re.finditer(r'\b'+alias+r'\b', body))
                if decls or any(not re.match(r'\[0\]', body[m.end():]) for m in uses):
                    failed = True; break
                changes.extend((m.start(), m.end(), name+'.s') for m in uses)
                alias_rows.append({'alias': alias, 'type': 's32[]', 'inferred': True})
                continue
            if not decls and not any(a.opcode == 'lw' and a.address ==
                    dataflow.Value.address('stack', frame+offset+off)
                    for a in flow.accesses.values()):
                failed = True; break
            uses = [m for m in re.finditer(r'\b'+alias+r'\b', body)
                    if not any(d.start() <= m.start() < d.end() for d in decls)]
            if any(re.search(r'&\s*$', body[:m.start()]) or re.match(r'\s*[\[.]', body[m.end():]) for m in uses):
                failed = True; break
            changes.extend((d.start(), d.end(), '') for d in decls)
            changes.extend((m.start(), m.end(), name+'.'+('u' if typ == 'u32' else 's')+f'[{off//4}]') for m in uses)
            alias_rows.append({'alias': alias, 'type': typ, 'inferred': not bool(decls)})
        if failed:
            continue
        if not anchor:
            changes.append((access.start(), access.end(), f'{name}.s[{iv}]'))
        changes.append((0, 0, f'\n    union {{ s32 s[{count}]; u32 u[{count}]; }} {name};\n'))
        edits.extend(changes); spans.append((offset, offset+4*count))
        report['changes'].append({'offset': offset, 'count': count, 'aliases': alias_rows, 'witness': witnesses[0]})
    for start, stop, replacement in sorted(edits, reverse=True):
        source = source[:definition.end()+start]+replacement+source[definition.end()+stop:]
    report['source'] = source
    return report
