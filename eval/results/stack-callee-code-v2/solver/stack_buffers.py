"""Callsite-bound byte-buffer hypotheses from target stack-frame spacing.

An observed address and the next occupied stack slot bound a candidate span;
they do NOT prove an original C array or its extent. Only address-only scalar
byte locals at uniquely aligned direct callsites are changed. Every proposal
must still compile and pass the ordinary independent verification gates.
"""
from collections import Counter
import hashlib
import re

from solver import dataflow, project_headers


def candidates(source, assembly, function, limit=4):
    from solver.repair_context import definition
    report = {'kind':'stack-buffer-hypotheses', 'plans':[], 'declined':[],
        'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
        'authority':'binary stack spacing; C extent is a hypothesis, not a proven allocation'}
    try:
        match, end = definition(source, function)
        analysis = dataflow.analyse(assembly)
    except ValueError as exc:
        return [], {**report, 'reason':str(exc)}
    instructions = analysis.graph.instructions
    if not instructions or instructions[0].opcode != 'addiu':
        return [], report
    operands = tuple(dataflow.reg(x) for x in instructions[0].operands)
    if len(operands) != 3 or operands[:2] != ('sp','sp'):
        return [], report
    frame_delta = dataflow.number(operands[2])
    if frame_delta is None or not -4096 <= frame_delta < 0:
        return [], report
    # Changes to SP other than one prologue and matching epilogues are outside
    # this dialect; don't mistake an alloca/secondary frame for one local array.
    for i in instructions[1:]:
        if i.operands and dataflow.reg(i.operands[0]) == 'sp' and i.opcode in dataflow.WRITES_FIRST:
            if i.opcode != 'addiu' or tuple(map(dataflow.reg,i.operands[:2])) != ('sp','sp') or dataflow.number(i.operands[2]) != -frame_delta:
                return [], {**report, 'reason':'dynamic/secondary stack frame'}
    occupied = sorted({0, *(a.address.offset for a in analysis.accesses.values()
                            if a.address and a.address.kind=='address' and a.address.name=='stack')})
    calls = list(analysis.callsites.values())
    counts = Counter(c.target for c in calls)
    masked = project_headers._mask_noncode(source)
    body = masked[match.end():end-1]
    plans = {}
    for call in calls:
        if not call.target or counts[call.target] != 1:
            continue
        # Ordinary comma-separated arguments only; nested expressions decline.
        sites = list(re.finditer(r'\b'+re.escape(call.target)+r'\s*\(([^()]*)\)',body))
        if len(sites) != 1:
            continue
        arguments = sites[0][1].split(',')
        for index, value in enumerate(call.arguments):
            if index >= len(arguments) or not value or value.kind!='address' or value.name!='stack':
                continue
            pointer = re.fullmatch(r'\s*&\s*([A-Za-z_]\w*)\s*',arguments[index])
            if not pointer or not frame_delta+16 <= value.offset < 0:
                continue
            variable = pointer[1]
            decls = list(re.finditer(r'(?m)^[ \t]*(?:u8|s8|char|unsigned\s+char|signed\s+char)\s+('+re.escape(variable)+r')\s*;',body))
            if len(decls) != 1:
                continue
            declaration = decls[0]
            others = body[:declaration.start()]+(' '*(declaration.end()-declaration.start()))+body[declaration.end():]
            # Only a complete address-of call argument. In particular, do not
            # turn logical/bitwise AND, sizeof(&x), or pointer escapes into decay.
            addresses = list(re.finditer(r'(?<=[(,])\s*(&\s*'+re.escape(variable)+r')\s*(?=[,)])',others))
            if len(addresses) != len(re.findall(r'\b'+re.escape(variable)+r'\b',others)):
                report['declined'].append({'variable':variable,'reason':'non-address-only scalar use'})
                continue
            boundary = next((p for p in occupied if p > value.offset), 0)
            extent = boundary-value.offset
            if not 2 <= extent <= 256:
                continue
            plan = {'variable':variable, 'extent':extent, 'callee':call.target,
                'argument':index, 'instruction':call.instruction,
                'entry_sp_offset':value.offset, 'next_occupied_entry_sp_offset':boundary}
            if variable in plans and plans[variable][0] is None:
                continue
            if variable in plans and plans[variable][0]['extent'] != extent:
                plans[variable] = (None,None,None)
                continue
            plans[variable] = (plan, declaration, addresses)
    rows = []
    for plan, declaration, addresses in plans.values():
        if plan is None or len(rows) >= limit:
            continue
        # Address decay preserves all &byte call operands; declaration/type and
        # control flow outside this one storage hypothesis remain untouched.
        changes = [(match.end()+declaration.end(1), match.end()+declaration.end(1),
                    '['+str(plan['extent'])+']')]
        changes += [(match.end()+a.start(1),match.end()+a.end(1),plan['variable']) for a in addresses]
        child = source
        for start,stop,text in sorted(changes,reverse=True):
            child = child[:start]+text+child[stop:]
        report['plans'].append(plan)
        rows.append(('stack-buffer-'+plan['variable'],child))
    return rows, report
