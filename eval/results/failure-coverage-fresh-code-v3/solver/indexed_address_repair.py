"""Measured global-member indexes plus bounded binary address witnesses.

Only two closed shapes are admitted: a full address in a direct-call delay slot,
or HI/shift/add/LO scalar load. Unsupported CFG, arithmetic or provenance declines.
The result is a source candidate, not a declaration of table extent or semantics.
"""
import hashlib
import re
import subprocess
from solver import cfg, dataflow, m2c_byte_view, project_headers, repair_context


def recover(repo, ws, source, function, assembly, target):
    """Cheap syntax trigger, fresh compiler measurement, then guarded proposal."""
    report = {'source': source, 'changes': [], 'declines': [],
        'source_sha256': hashlib.sha256(source.encode()).hexdigest()}
    mask = project_headers._mask_noncode(source)
    if not re.search(r'\(\s*\w+(?:\.\w+)+\s*\*\s*(?:0x[0-9A-Fa-f]+|[1-9][0-9]*)\s*\)\s*\+\s*&|\*\(\s*&\s*\w+\s*\+\s*\(\s*\w+\.', mask):
        return report
    from solver import type_constraints
    try:
        measured = type_constraints.measure(repo, ws, source, function, target)
        return propose(source, function, assembly, measured)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        report['declines'].append('indexed-address measurement/proposal unavailable: '+str(exc))
        return report


def witnesses(assembly):
    flow = dataflow.analyse(assembly)
    instructions = flow.graph.instructions
    def prior_definition(at, register):
        block = flow.graph.instruction_to_block[at]
        for i in range(at-1, max(-1, at-17), -1):
            insn = instructions[i]
            if flow.graph.instruction_to_block[i] != block:
                return None
            if insn.opcode in cfg.CALL_OPS:
                # The call has not clobbered registers before its delay slot.
                if i == at-1 and flow.callsites.get(i) and flow.callsites[i].delay_slot == at:
                    continue
                return None
            if dataflow._destination(insn) == register:
                return insn
        return None
    rows = []
    for insn in instructions:
        if insn.opcode != 'addu' or len(insn.operands) != 3 or insn.index not in flow.instruction_in:
            continue
        dest, left, right = map(dataflow.reg, insn.operands)
        for base_reg, index_reg in [(left, right), (right, left)]:
            base_def = prior_definition(insn.index, base_reg)
            shift = prior_definition(insn.index, index_reg)
            if not base_def or not shift or shift.opcode != 'sll' or len(shift.operands) != 3:
                continue
            amount = dataflow.number(shift.operands[2])
            index = flow.instruction_in[shift.index].registers.get(dataflow.reg(shift.operands[1]))
            if amount is None or not 0 <= amount <= 8 or not index or index.kind != 'load' or index.offset != 0:
                continue
            if not index.inner or index.inner.kind != 'address' or index.inner.name in {'stack','gp'} or index.inner.name.startswith('param'):
                continue
            common = {'add_instruction': insn.index, 'shift_instruction': shift.index,
                'index_global': index.inner.name, 'index_offset': index.inner.offset,
                'index_width': index.width, 'index_signed': index.signed, 'stride': 1 << amount}
            # Complete symbolic address: HI + matching LO, not just a HI page.
            if base_def.opcode == 'addiu' and len(base_def.operands) == 3:
                lo = re.fullmatch(r'%lo\((\w+)\)', base_def.operands[2])
                hi = prior_definition(base_def.index, dataflow.reg(base_def.operands[1]))
                call = flow.callsites.get(insn.index-1)
                if (lo and hi and hi.opcode == 'lui' and hi.operands[1] == '%hi('+lo[1]+')'
                        and call and call.delay_slot == insn.index and call.target and dest in {'a0','a1','a2','a3'}):
                    rows.append({**common, 'kind': 'call-address', 'table': lo[1],
                        'callee': call.target, 'argument_word': int(dest[1]), 'call_instruction': call.instruction})
            # HI base is completed by LO on the following memory operation.
            if base_def.opcode == 'lui' and len(base_def.operands) == 2 and insn.index+1 < len(instructions):
                hi = re.fullmatch(r'%hi\((\w+)\)', base_def.operands[1])
                load = instructions[insn.index+1]
                mem = dataflow.RELOC_MEMORY.fullmatch(', '.join(load.operands))
                if (hi and mem and mem['symbol'] == hi[1] and not mem['addend']
                        and dataflow.reg(mem['base']) == dest and load.opcode in {'lb','lbu','lh','lhu','lw'}
                        and flow.graph.instruction_to_block[load.index] == flow.graph.instruction_to_block[insn.index]):
                    rows.append({**common, 'kind': 'scalar-load', 'table': hi[1],
                        'load_instruction': load.index, 'load_width': dataflow.LOAD_WIDTH[load.opcode],
                        'load_signed': load.opcode in {'lb','lh'} if load.opcode != 'lw' else None})
    return rows


def propose(source, function, assembly, measurement):
    identity = hashlib.sha256(source.encode()).hexdigest()
    report = {'source': source, 'changes': [], 'declines': [], 'source_sha256': identity,
        'assembly_sha256': hashlib.sha256(assembly.encode()).hexdigest(),
        'authority': 'measured index plus binary byte-stride candidate; no table extent or semantic proof'}
    if measurement.get('kind') != 'target-compiler-header-layouts' or measurement.get('source_sha256') != identity:
        report['declines'].append('missing or stale compiler measurement')
        return report
    definition, end = repair_context.definition(source, function)
    mask = project_headers._mask_noncode(source)
    body = mask[definition.end():end-1]
    binary = witnesses(assembly)
    scalar = {'s8': (1, True), 'u8': (1, False), 's16': (2, True), 'u16': (2, False),
              's32': (4, None), 'u32': (4, None)}
    def matches(global_, member, literal, table, kind):
        declarations = [g for g in measurement.get('global_declarations', []) if g['name'] == global_]
        types = {g['spelling'] for g in declarations}
        if len(types) != 1:
            return []
        fields = [f for f in measurement.get('layouts', {}).get(next(iter(types)), []) if f['member'] == member]
        if len(fields) != 1 or fields[0].get('spelling') not in scalar:
            return []
        field = fields[0]
        width, signed = scalar[field['spelling']]
        if field.get('width') != width or field.get('pointer') or field.get('array'):
            return []
        # Source member edits or escapes may disconnect the sampled binary load.
        if re.search(r'\b'+global_+r'\s*(?:\.[\w.]+\s*)?(?:[+\-*/%&|^]?=(?!=)|\+\+|--)|&\s*\b'+global_+r'\b', body):
            return []
        return [w for w in binary if w['kind'] == kind and w['table'] == table
            and w['index_global'] == global_ and w['index_offset'] == field.get('offset')
            and w['index_width'] == width and w['index_signed'] == signed and w['stride'] == int(literal, 0)]
    edits = []
    number = r'(0x[0-9A-Fa-f]+|[1-9][0-9]*)'
    index = r'(\w+)\.(\w+(?:\.\w+)*)\s*\*\s*'+number
    for m in re.finditer(r'\*\(\s*&\s*(\w+)\s*\+\s*\(\s*'+index+r'\s*\)\s*\)', body):
        table, global_, member, literal = m.groups()
        declarations = re.findall(r'(?m)^\s*extern\s+(s8|u8|s16|u16|s32|u32)\s+'+table+r'\s*;', mask[:definition.start()])
        peers = matches(global_, member, literal, table, 'scalar-load')
        if len(declarations) != 1 or len(peers) != 1 or scalar[declarations[0]] != (peers[0]['load_width'], peers[0]['load_signed']):
            report['declines'].append({'expression': m[0], 'reason': 'nonunique scalar-load/type/index witness'})
            continue
        replacement = f'*({declarations[0]} *)((u8 *)&{table} + ({global_}.{member} * {literal}))'
        edits.append((m.start(), m.end(), replacement))
        report['changes'].append({'before': m[0], 'after': replacement, 'witness': peers[0]})
    for call in re.finditer(r'\b(\w+)\s*\(', body):
        if not any(w.get('callee') == call[1] for w in binary):
            continue
        stop = m2c_byte_view.closing(body, call.end()-1)
        args = m2c_byte_view.arguments(body[call.end():stop])
        cursor = call.end()
        for word, arg in enumerate(args):
            start = body.find(arg, cursor, stop)
            cursor = start+len(arg)
            m = re.fullmatch(r'\(\s*'+index+r'\s*\)\s*\+\s*&\s*(\w+)', arg)
            if not m:
                continue
            global_, member, literal, table = m.groups()
            peers = [w for w in matches(global_, member, literal, table, 'call-address')
                if w['callee'] == call[1] and w['argument_word'] == word]
            if len(peers) != 1 or body.count(arg) != 1:
                report['declines'].append({'expression': arg, 'reason': 'nonunique call/index witness'})
                continue
            replacement = f'(void *)((u8 *)&{table} + ({global_}.{member} * {literal}))'
            edits.append((start, start+len(arg), replacement))
            report['changes'].append({'before': arg, 'after': replacement, 'witness': peers[0]})
    candidate = source
    for start, stop, replacement in sorted(edits, reverse=True):
        candidate = candidate[:definition.end()+start]+replacement+candidate[definition.end()+stop:]
    report.update(source=candidate, candidate_sha256=hashlib.sha256(candidate.encode()).hexdigest(), measurement=measurement)
    return report
