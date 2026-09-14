"""Fill local declaration holes without replacing an inferred argument list.

Candidate-only: caller constants constrain word slots; callee returned-address
use constrains a pointer return. No shared header, runtime contract or KB writes.
"""
import hashlib
import re
import subprocess

from solver import cfg, dataflow, m2c_byte_view, project_headers, repair_context, target_intake


def missing_pointer_interfaces(repo,source,function,assembly,diagnostics):
    """Local declarations for bounded pointer-returning callback wrappers.

    No call arguments are changed; no interface is installed in runtime models.
    """
    from solver import m2c_input
    definition,end=repair_context.definition(source,function)
    mask=project_headers._mask_noncode(source); body=mask[definition.end():end-1]
    names=sorted(set(re.findall(r"implicit declaration of function '([A-Za-z_]\w*)'",diagnostics)))
    report={'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'changes':[],'declines':[], 'authority':'local inferred interface; compiler/runtime gates remain required'}
    additions=[]; flow=dataflow.analyse(assembly)
    for name in names[:2]:
        try:
            if project_headers.declarations(repo,name) or re.search(r'\b'+name+r'\s*\(',mask[:definition.start()]):
                raise ValueError('existing declaration takes precedence')
            resolved=target_intake.resolve(repo,name)
            if resolved.kind!='disassembly' or resolved.symbol!=name:
                raise ValueError('requires uniquely named target disassembly')
            callee=resolved.path.read_text()
            if len(callee)>32768 or len(cfg.build(callee).instructions)>256:
                raise ValueError('callee exceeds bounded probe')
            returns=returned_address_evidence(callee)
            if not returns:raise ValueError('returned address evidence unavailable')
            headers=tuple(dict.fromkeys(['common.h',*re.findall(r'(?m)^\s*#include\s+"([^"\n]+)"',source)]))
            draft,meta=m2c_input.draft(repo,resolved.path,context_headers=headers,valid_syntax=True)
            if draft.returncode:raise ValueError('callee draft failed')
            signature,_=repair_context.definition(draft.stdout,name)
            params=m2c_byte_view.arguments(signature[2])
            if signature[1].strip()!='void *' or not 1<=len(params)<=4:
                raise ValueError('requires bounded pointer-returning wrapper')
            types=[]
            for i,param in enumerate(params):
                if i==0 and re.fullmatch(r'void\s*\(\s*\*\s*\w+\s*\)\s*\(\s*void\s*\*\s*\)',param):
                    types.append('void (*)(void *)')
                elif re.fullmatch(r'(s8|u8|s16|u16|s32|u32)\s+\w+',param.strip()):
                    types.append(param.strip().split()[0])
                else:raise ValueError('unsupported inferred parameter shape')
            if types[0]!='void (*)(void *)':raise ValueError('requires callback first parameter')
            calls=list(re.finditer(r'\b'+name+r'\s*\(',body))
            targets=[c for c in flow.callsites.values() if c.target==name]
            if not calls or len(calls)!=len(targets):raise ValueError('source/target call count mismatch')
            for call in calls:
                stop=m2c_byte_view.closing(body,call.end()-1)
                if len(m2c_byte_view.arguments(body[call.end():stop]))!=len(types):
                    raise ValueError('caller arity differs; do not add or remove slots')
            declaration='extern void *'+name+'('+', '.join(types)+');\n'
            additions.append(declaration)
            report['changes'].append({'name':name,'declaration':declaration,'body_unchanged':True,
                'callee_path':str(resolved.path),'callee_sha256':hashlib.sha256(callee.encode()).hexdigest(),
                'returned_addresses':returns,'caller_instructions':[c.instruction for c in targets],
                'draft':meta,'generated_source_sha256':hashlib.sha256(draft.stdout.encode()).hexdigest()})
        except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
            report['declines'].append({'name':name,'reason':str(exc)})
    source=source[:definition.start()]+''.join(additions)+source[definition.start():]
    report['candidate_sha256']=hashlib.sha256(source.encode()).hexdigest()
    return source,report


def returned_address_evidence(assembly):
    flow = dataflow.analyse(assembly)
    returns = []
    for block in flow.graph.blocks.values():
        term = block.terminator
        if term and term.opcode == 'jr' and tuple(map(dataflow.reg, term.operands)) == ('ra',):
            state = flow.block_out.get(block.id)
            value = state.registers.get('v0') if state else None
            if value is None or value.kind != 'call_result' or value.offset:
                return []
            sites = [i for i, access in flow.accesses.items()
                     if access.address and access.address.kind == 'call_result'
                     and access.address.site == value.site]
            if not sites:
                return []
            returns.append({'return_instruction': term.index,
                            'returned_call': value.site, 'address_uses': sites})
    return returns


def propose(repo, source, function, assembly):
    report = {'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
              'assembly_sha256': hashlib.sha256(assembly.encode()).hexdigest(),
              'changes': [], 'declines': [],
              'authority': 'local declaration hypothesis, not proven ABI or semantics'}
    definition, end = repair_context.definition(source, function)
    mask = project_headers._mask_noncode(source)
    body = mask[definition.end():end-1]
    pattern = r'(?m)^[ \t]*(?:extern\s+)?M2C_UNK\s+(\w+)\s*\(([^;{}\n]*)\)\s*;'
    declarations = list(re.finditer(pattern, mask[:definition.start()]))
    if not declarations:
        report['candidate_sha256'] = report['source_sha256']
        return source, report
    flow = dataflow.analyse(assembly)
    edits = []
    report['omitted_names'] = [d[1] for d in declarations[2:]]
    for declaration in declarations[:2]:
        name = declaration[1]
        try:
            params = m2c_byte_view.arguments(declaration[2])
            holes = [i for i, p in enumerate(params) if p == 'M2C_UNK']
            if not holes or not 1 <= len(params) <= 4 or '...' in declaration[2]:
                raise ValueError('requires fixed one-word argument holes')
            if any('M2C_UNK' in p and p != 'M2C_UNK' for p in params):
                raise ValueError('nested unknown type')
            if project_headers.declarations(repo, name):
                raise ValueError('existing header declaration takes precedence')
            source_calls = list(re.finditer(r'(?m)^[ \t]*'+re.escape(name)+r'\s*\(', body))
            binary_calls = sorted((c for c in flow.callsites.values() if c.target == name),
                                  key=lambda c: c.instruction)
            if not source_calls or len(source_calls) != len(binary_calls):
                raise ValueError('requires matched standalone calls')
            if len(re.findall(r'\b'+re.escape(name)+r'\b', mask)) != len(source_calls)+1:
                raise ValueError('other uses or declarations of callee')
            witnesses = []
            for call, binary in zip(source_calls, binary_calls):
                closing = m2c_byte_view.closing(body, call.end()-1)
                args = m2c_byte_view.arguments(body[call.end():closing])
                if not re.match(r'\s*;', body[closing+1:]) or len(args) != len(params):
                    raise ValueError('call arity or discarded-result mismatch')
                for i in holes:
                    if not re.fullmatch(r'(?:0x[0-9a-fA-F]+|[0-9]+)', args[i]):
                        raise ValueError('unknown slot needs a plain signed-word constant')
                    value = int(args[i], 16 if args[i].lower().startswith('0x') else 10)
                    if not 0 <= value <= 0x7fffffff or binary.arguments[i] != dataflow.Value.constant(value):
                        raise ValueError('caller register constant mismatch')
                    witnesses.append({'instruction': binary.instruction, 'slot': i, 'value': value})
            resolved = target_intake.resolve(repo, name)
            if resolved.kind != 'disassembly' or resolved.symbol != name:
                raise ValueError('requires uniquely named target disassembly')
            callee = resolved.path.read_text()
            if len(callee) > 32768 or len(cfg.build(callee).instructions) > 256:
                raise ValueError('callee exceeds bounded probe')
            returns = returned_address_evidence(callee)
            if not returns:
                raise ValueError('returned pointer evidence unavailable')
            replacement = 'void *'+name+'('+', '.join('s32' if i in holes else p
                                                     for i, p in enumerate(params))+');'
            edits.append((declaration.start(), declaration.end(), replacement))
            report['changes'].append({'name': name, 'before': declaration[0], 'after': replacement,
                'caller_constants': witnesses, 'callee_returns': returns,
                'callee_path': str(resolved.path),
                'callee_sha256': hashlib.sha256(callee.encode()).hexdigest(),
                'preserved_argument_slots': len(params), 'body_unchanged': True})
        except (OSError, ValueError, RuntimeError) as exc:
            report['declines'].append({'name': name, 'reason': str(exc)})
    for start, stop, replacement in sorted(edits, reverse=True):
        source = source[:start]+replacement+source[stop:]
    report['candidate_sha256'] = hashlib.sha256(source.encode()).hexdigest()
    return source, report
