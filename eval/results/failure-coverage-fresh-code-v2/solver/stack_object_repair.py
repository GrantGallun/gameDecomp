"""Candidate-only coalescing of m2c split stack locals; never a layout proof.

Requires a source-bound compiler measurement, existing aggregate cast, resolved
target stack call addresses and stores. spHEX names remain correspondence
hypotheses. Compiler and differential validation must adjudicate every proposal.
"""
import hashlib
import re

from solver import dataflow, project_headers, repair_context, source_object_bounds


def propose(source, function, assembly, measurement):
    identity = hashlib.sha256(source.encode()).hexdigest()
    report = {'source': source, 'source_sha256': identity,
        'assembly_sha256': hashlib.sha256(assembly.encode()).hexdigest(),
        'changes': [], 'declines': [],
        'authority': 'header/binary-constrained candidate; stack-name correspondence is a hypothesis'}
    obligations = source_object_bounds.obligations(source, function)
    if not obligations:
        return report
    if (measurement.get('kind') != 'target-compiler-header-layouts'
            or measurement.get('source_sha256') != identity):
        report['declines'].append('missing or stale source-bound compiler measurement')
        return report
    try:
        definition, end = repair_context.definition(source, function)
        flow = dataflow.analyse(assembly)
    except ValueError as exc:
        report['declines'].append(str(exc))
        return report
    masked = project_headers._mask_noncode(source)
    body = masked[definition.end():end-1]
    # Require one observed nonzero frame, including at every admitted call.
    frames = {s.registers['sp'].offset for s in flow.instruction_in.values()
        if s.registers.get('sp') is not None and s.registers['sp'].kind == 'address'
        and s.registers['sp'].name == 'stack' and s.registers['sp'].offset != 0}
    if len(frames) != 1 or next(iter(frames)) >= 0:
        report['declines'].append('no unique fixed target stack frame')
        return report
    frame = next(iter(frames))
    widths = {'s8': 1, 'u8': 1, 's16': 2, 'u16': 2, 's32': 4, 'u32': 4,
              'f32': 4, 's64': 8, 'u64': 8, 'f64': 8}
    declarations = list(re.finditer(r'(?m)^[ \t]*(\w+)\s+(sp[0-9A-Fa-f]+)\s*;', body))
    def calls_at(callee, base):
        return [c.instruction for c in flow.callsites.values()
            if c.target == callee and c.arguments[0] == dataflow.Value.address('stack', base)
            and flow.instruction_in[c.instruction].registers.get('sp') == dataflow.Value.address('stack', frame)]
    for obligation in obligations:
        local = obligation.get('local', '')
        def decline(reason):
            report['declines'].append({'local': local, 'reason': reason})
        roots = [d for d in declarations if d[2] == local]
        if len(roots) != 1 or not re.fullmatch(r'sp[0-9A-Fa-f]+', local):
            decline('requires unique plain spHEX scalar declaration')
            continue
        root = roots[0]
        casts = list(re.finditer(r'\((\w+)\s*\*\)\s*&\s*'+local+r'\b', body))
        types = {m[1] for m in casts if m[1] in measurement.get('layouts', {})}
        if len(types) != 1:
            decline('requires unique existing measured aggregate cast')
            continue
        typ = next(iter(types))
        fields = measurement['layouts'][typ]
        sizes = {f.get('owner_size') for f in fields}
        if len(sizes) != 1 or not all(isinstance(f.get('offset'), int) and isinstance(f.get('width'), int)
                and f['width'] > 0 and re.fullmatch(r'\w+(?:\.\w+)*', f.get('member', '')) for f in fields):
            decline('incomplete measured layout')
            continue
        size = next(iter(sizes))
        if not isinstance(size, int) or size < obligation['required_end_offset'] or obligation['minimum_accessed_offset'] < 0:
            decline('measured object does not contain byte-view accesses')
            continue
        if any(f['offset'] < 0 or f['offset'] + f['width'] > size for f in fields) or any(
                max(a['offset'], b['offset']) < min(a['offset']+a['width'], b['offset']+b['width'])
                for i, a in enumerate(fields) for b in fields[i+1:]):
            decline('overlapping or out-of-object measured fields')
            continue
        offset = int(local[2:], 16)
        base = frame + offset
        if offset < 0 or base + size > 0:
            decline('object outside target frame')
            continue
        slots = {m[0] for m in re.finditer(r'\bsp[0-9A-Fa-f]+\b', body)
            if offset <= int(m[0][2:], 16) < offset+size}
        if any(sum(d[2] == slot for d in declarations) != 1 for slot in slots):
            decline('unrecognized or shadowed local inside proposed object')
            continue
        # Existing typed first-argument calls tie the name hypothesis to binary.
        typed_calls = list(re.finditer(r'\b(\w+)\s*\(\s*\('+typ+r'\s*\*\)\s*&\s*'+local+r'\b', body))
        witnesses = {m[1]: calls_at(m[1], base) for m in typed_calls}
        if not typed_calls or any(len(sites) < sum(m[1] == callee for m in typed_calls)
                for callee, sites in witnesses.items()):
            decline('no resolved target stack argument for aggregate cast')
            continue
        # No scalar root reads/writes, shadowing or unsupported address syntax.
        root_uses = list(re.finditer(r'\b'+local+r'\b', body))
        if any(not (root.start() <= m.start() < root.end()) and not re.search(r'&\s*$', body[:m.start()]) for m in root_uses):
            decline('non-address root use')
            continue
        edits = [(root.start(), root.end(), root[0].replace(root[1], typ, 1))]
        first_arrays = [f for f in fields if f['offset'] == 0 and f.get('array')
            and re.fullmatch(re.escape(root[1])+r'\s*\[\s*\d+\s*\]', f.get('canonical', ''))]
        bare_calls = list(re.finditer(r'\b(\w+)\s*\(\s*(&\s*'+local+r'\b)', body))
        if bare_calls and (len(first_arrays) != 1 or any(not calls_at(m[1], base) for m in bare_calls)):
            decline('bare scalar-pointer call lacks unique leading array and target address')
            continue
        for m in bare_calls:
            edits.append((m.start(2), m.end(2), local+'.'+first_arrays[0]['member']))
            witnesses[m[1]] = calls_at(m[1], base)
        merges = []
        failed = False
        for d in declarations:
            if d[2] == local or not offset <= int(d[2][2:], 16) < offset+size:
                continue
            relative = int(d[2][2:], 16)-offset
            matches = [f for f in fields if f['offset'] == relative and f['width'] == widths.get(d[1])
                and f.get('spelling') == d[1]
                and not f.get('pointer') and not f.get('array')]
            stores = [a.instruction for a in flow.accesses.values() if not a.is_load
                and a.opcode in {'sb','sh','sw','sd','swc1','sdc1'}
                and a.address == dataflow.Value.address('stack', base+relative) and a.width == widths.get(d[1])]
            uses = [m for m in re.finditer(r'\b'+d[2]+r'\b', body) if not d.start() <= m.start() < d.end()]
            # Assignment-only split locals; reading, escaping or redeclaring
            # could join distinct lifetimes. Leave such cases to another pass.
            if len(matches) != 1 or not stores or not uses or any(
                    not re.match(r'\s*=(?!=)', body[m.end():]) or
                    (body[:m.start()].rstrip() and body[:m.start()].rstrip()[-1] not in ';{}:') for m in uses):
                failed = True
                break
            edits.append((d.start(), d.end(), ''))
            member = local+'.'+matches[0]['member']
            edits.extend((m.start(), m.end(), member) for m in uses)
            merges.append({'local': d[2], 'member': matches[0]['member'], 'target_store_instructions': stores})
        if failed or not merges:
            decline('split locals lack closed assignment uses or measured/binary slots')
            continue
        candidate = source
        for start, stop, replacement in sorted(edits, reverse=True):
            candidate = candidate[:definition.end()+start]+replacement+candidate[definition.end()+stop:]
        report.update(source=candidate, candidate_sha256=hashlib.sha256(candidate.encode()).hexdigest(),
            measurement=measurement, changes=[{'local': local, 'type': typ, 'frame': frame,
                'target_object_base': base, 'calls': witnesses, 'merged_locals': merges,
                'obligation': obligation}])
        return report  # one object per bounded proposal
    return report
