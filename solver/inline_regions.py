"""Binary-only repeated-region hints; similarity never proves original inlining.

The first version deliberately matches straight-line integer regions. Calls,
branches (including their delay slots), stack operations and internal labels
split regions. Register renaming preserves reuse, constants and symbol names.
No reference C is loaded and no match is an equivalence certificate.
"""
import hashlib
import re
from collections import defaultdict

from solver import cfg

REG = re.compile(r'(?<![\w.$])\$?(?:zero|at|v[01]|a[0-3]|t[0-9]|s[0-8]|k[01]|gp|sp|fp|ra|f\d+)(?![\w.$])')
OPS = {'addu','addiu','subu','and','andi','or','ori','xor','xori','nor',
       'sll','srl','sra','sllv','srlv','srav','slt','sltu','slti','sltiu',
       'lui','li','move','neg','negu','not','lb','lbu','lh','lhu','lw','sb','sh','sw'}


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def signature(instructions):
    names = {}
    def rename(match):
        reg = match[0].lstrip('$')
        if reg in {'zero','sp','gp','ra','fp'}:
            return reg
        return names.setdefault(reg, 'r'+str(len(names)))
    def operand(op):
        # A symbol may itself be named t0; relocation operands are not registers.
        return ''.join(part if part.startswith('%') else REG.sub(rename, part)
                       for part in re.split(r'(%\w+\([^)]*\))', op))
    return tuple(i.opcode+' '+','.join(operand(op) for op in i.operands)
                 for i in instructions)


def runs(assembly):
    instructions, _ = cfg.parse_assembly(assembly)
    result, current = [], []
    delay = False
    for ins in instructions:
        skip = (delay or ins.opcode not in OPS or
                any(m[0].lstrip('$') in {'sp','fp','gp','ra'}
                    for op in ins.operands for m in REG.finditer(op)))
        if (skip or ins.labels) and current:
            result.append(tuple(current))
            current = []
        if not skip:
            current.append(ins)
        delay = cfg.has_delay_slot(ins.opcode)
    if current:
        result.append(tuple(current))
    return instructions, result


def analyse(assemblies, min_instructions=8, large_instructions=128):
    if not 4 <= min_instructions <= 32 or large_instructions < min_instructions:
        raise ValueError('invalid region thresholds')
    parsed = {name:runs(asm) for name,asm in sorted(assemblies.items())}
    hashes = {name:sha(asm) for name,asm in assemblies.items()}
    index = defaultdict(list)
    for name, (_, regions) in parsed.items():
        for region in regions:
            for start in range(len(region)-min_instructions+1):
                chunk = region[start:start+min_instructions]
                # Repeated constant initialization and register moves alone are
                # too weak to justify a helper hypothesis.
                if len({i.opcode for i in chunk}) < 3:
                    continue
                index[signature(chunk)].append((name,region,start))
    patterns = []
    for sig, entries in index.items():
        distinct, ends = [], {}
        for name,region,start in entries:
            if region[start].index >= ends.get(name, -1):
                distinct.append((name,region,start))
                ends[name] = region[start].index+min_instructions
        entries = distinct
        if len(entries) < 2 or not any(len(parsed[n][0]) >= large_instructions for n,_,_ in entries):
            continue
        occurrences = []
        for name, region, start in entries:
            chunk = region[start:start+min_instructions]
            occurrences.append({'function':name, 'start_instruction':chunk[0].index,
                'end_instruction_exclusive':chunk[-1].index+1,
                'assembly_sha256':hashes[name]})
        patterns.append({'signature_sha256':sha('\n'.join(sig)),
            'instruction_count':min_instructions, 'function_count':len({e[0] for e in entries}),
            'occurrence_count':len(entries), 'occurrences':occurrences[:16],
            'occurrences_truncated':len(entries)>16,
            'example':[i.text for i in entries[0][1][entries[0][2]:entries[0][2]+min_instructions]]})
    patterns.sort(key=lambda row:(-row['function_count'],-row['occurrence_count'],row['signature_sha256']))
    # Whole eligible leaf bodies provide a stronger (still hypothetical) witness
    # than equal windows. Return delay slots must be nop; real work there is not
    # silently dropped or moved across the return.
    helpers = []
    for name,(instructions,regions) in parsed.items():
        if not (min_instructions+2 <= len(instructions) < large_instructions):
            continue
        if instructions[-2].opcode != 'jr' or instructions[-2].operands not in {('ra',),('$ra',)} or instructions[-1].opcode != 'nop':
            continue
        body = instructions[:-2]
        if len(regions)!=1 or tuple(body)!=regions[0] or len(body)>64:
            continue
        sig = signature(body)
        for caller, (other, other_regions) in parsed.items():
            if caller == name or len(other) < large_instructions:
                continue
            for region in other_regions:
                for start in range(len(region)-len(body)+1):
                    if signature(region[start:start+len(body)]) == sig:
                        helpers.append({'helper':name,'caller':caller,
                            'helper_assembly_sha256':hashes[name],
                            'caller_assembly_sha256':hashes[caller],
                            'start_instruction':region[start].index,
                            'instruction_count':len(body)})
    return {'schema_version':1,'scope':'Straight-line region similarity; possible shared idiom, macro, or inlined helper. Not proof of inlining or equivalence. No C source used.',
            'min_instructions':min_instructions,'large_instructions':large_instructions,
            'functions':len(parsed),'large_functions':sum(len(v[0])>=large_instructions for v in parsed.values()),
            'pattern_count':len(patterns),'patterns':patterns[:100],
            'embedded_helper_count':len(helpers),'embedded_helpers':helpers[:100]}


def prompt(assembly):
    """Bounded within-caller hints, using the exact assembly already in prompt."""
    report = analyse({'current':assembly})
    if not report['patterns']:
        return ''
    selected, used = [], set()
    for row in report['patterns']:
        starts = [o['start_instruction'] for o in row['occurrences']]
        if any(any(abs(s-u)<report['min_instructions'] for u in used) for s in starts):
            continue
        used.update(starts)
        selected.append('instruction indices '+', '.join(map(str,starts))+
                        ': '+str(row['instruction_count'])+' instructions with matching register-renamed shape')
        if len(selected)==3:
            break
    return ('\nREPEATED REGION HYPOTHESES (zero-based parsed instruction indices):\n'+
            '\n'.join(selected)+'\nThese may be repeated idioms, macros, or expanded helpers; original inlining is not established. '
            'Compare their local source forms and live inputs/outputs. Repair one occurrence at a time; '
            'preserve argument evaluation, types and effects. Do not introduce a helper call where target assembly has none. '
            'Compile and verify the entire caller; region similarity is not a correctness gate.\n')
