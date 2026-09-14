"""Source-bound address-unit candidates from resolved binary stores.

No pointee type or global extent is inferred. Existing source pointer views are
hypotheses; normal compiler and semantic gates retain authority.
"""
import hashlib
import re
from solver import dataflow, m2c_byte_view, project_headers, repair_context


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
    report['source'] = candidate
    report['candidate_sha256'] = hashlib.sha256(candidate.encode()).hexdigest()
    return report
