"""Bounded address-return hypotheses for source-independent m2c redrafts.

An address carried in v0 is not proof of the original C return type. Preserve
the ordinary draft; the compiler, frontend and declaration gates adjudicate
this alternative. No field, extent, ABI or KB fact is written.
"""
import hashlib
import re
from solver import cfg, dataflow, project_headers

WORD = ('word', '', frozenset())
BRANCHES = {'beq', 'bne', 'beqz', 'bnez', 'bgez', 'bgtz', 'blez', 'bltz', 'b', 'j'}
SCALARS = {'sll', 'srl', 'sra', 'sllv', 'srlv', 'srav', 'andi', 'ori', 'xori',
           'and', 'or', 'xor', 'nor', 'subu', 'sub', 'slt', 'sltu', 'slti', 'sltiu',
           'neg', 'negu', 'not', 'li'}
LOADS = {'lb', 'lbu', 'lh', 'lhu', 'lw', 'lwl', 'lwr'}
STORES = {'sb', 'sh', 'sw', 'swl', 'swr'}

def _apply(state, ins):
    op, args = ins.opcode, ins.operands
    if op in BRANCHES | STORES | {'nop', 'jr'}:
        return
    dest = dataflow.reg(args[0]) if args else ''
    get = lambda r: state.get(dataflow.reg(r), WORD)
    value = WORD
    if op == 'lui' and len(args) == 2:
        match = dataflow.HI_RELOC.fullmatch(args[1])
        if match:
            value = ('hi', match[1], frozenset({ins.index}))
    elif op == 'la' and len(args) == 2 and re.fullmatch(r'[A-Za-z_]\w*', args[1]):
        value = ('address', args[1], frozenset({ins.index}))
    elif op == 'move' and len(args) == 2:
        value = get(args[1])
    elif op in {'addiu', 'addi'} and len(args) == 3:
        base = get(args[1]); match = dataflow.LO_RELOC.fullmatch(args[2])
        if match and base[:2] == ('hi', match[1]):
            value = ('address', match[1], base[2] | {ins.index})
        elif dataflow.number(args[2]) is not None and base[0] == 'address':
            value = ('address', base[1], base[2] | {ins.index})
    elif op in {'addu', 'add', 'or'} and len(args) == 3:
        left, right = get(args[1]), get(args[2])
        if dataflow.reg(args[1]) == 'zero': value = right
        elif dataflow.reg(args[2]) == 'zero': value = left
        elif op in {'addu', 'add'} and {left[0], right[0]} == {'address', 'word'}:
            base = left if left[0] == 'address' else right
            value = ('address', base[1], base[2] | {ins.index})
    if dest and dest != 'zero': state[dest] = value

def propose(function, assembly, context, *, fixed_return=False):
    report = {'status':'declined', 'target_assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
              'authority':'address-flow witness and context types are candidate hypotheses; no original C type claim'}
    def decline(reason):
        return {**report, 'reason':reason}
    own = context['own_prototype']
    signature = re.fullmatch(r's32\s+'+re.escape(function)+r'\(([^;]*)\);', own)
    if fixed_return or not signature or context.get('fixed_return'):
        return decline('return declaration fixed, already pointer-valued, or outside provisional s32 domain')
    if context['declarations'].count(own) != 1:
        return decline('own provisional declaration is not unique')
    declarations = project_headers._mask_noncode(context['declarations'])
    if len(re.findall(r'\b'+re.escape(function)+r'\s*\(', declarations)) != 1:
        return decline('another declaration constrains the return')
    begin = re.search(r'(?m)^\s*glabel\s+'+re.escape(function)+r'\s*$', assembly)
    if not begin: return decline('target function boundary absent')
    body = re.split(r'(?m)^\s*(?:endlabel|glabel)\s+', assembly[begin.end():], maxsplit=1)[0]
    graph = cfg.build(body)
    reachable = graph.reachable()
    if not reachable or len(graph.instructions) > 2048 or len(reachable) > 64:
        return decline('empty or over-budget control flow')
    if any(len(part) > 1 or any(n in graph.blocks[n].successors for n in part)
           for part in graph.strongly_connected_components()):
        return decline('cyclic flow requires a stronger address analysis')
    allowed = BRANCHES | SCALARS | LOADS | STORES | {'lui','la','move','addiu','addi','addu','add','nop','jr'}
    for key in reachable:
        block = graph.blocks[key]
        if block.unknown_successor: return decline('unresolved successor')
        if block.terminator and cfg.has_delay_slot(block.terminator.opcode) and block.delay_slot is None:
            return decline('transfer delay slot is absent or split by a target label')
        if block.terminator and cfg.is_conditional_branch(block.terminator.opcode) and key+1 not in graph.blocks:
            return decline('conditional fallthrough leaves the function boundary')
        if block.delay_slot and cfg.is_control_transfer(block.delay_slot.opcode):
            return decline('control transfer in a delay slot')
        if any(i.opcode not in allowed for i in block.instructions):
            return decline('calls, likely branches or unsupported instructions')
        if block.terminator and block.terminator.opcode == 'jr':
            if block.terminator.operands != ('$ra',) and block.terminator.operands != ('ra',):
                return decline('indirect non-return transfer')
            if block.delay_slot is None: return decline('return delay slot absent')
        elif not block.successors:
            return decline('reachable fallthrough has no return')
    entry = {'zero':WORD}
    for arg in range(4):
        if re.search(r'\*\s*arg'+str(arg)+r'\b', signature[1]):
            entry['a'+str(arg)] = ('address', 'context:arg'+str(arg), frozenset())
    outputs, witnesses = {}, []
    for key in graph.reverse_postorder():
        block = graph.blocks[key]
        if key == graph.entry:
            state = dict(entry)
        else:
            predecessors = [outputs[p] for p in sorted(block.predecessors & reachable)]
            state = {}
            for reg in set().union(*(s.keys() for s in predecessors)):
                values = [s.get(reg, WORD) for s in predecessors]
                if all(v[0] == 'address' for v in values):
                    state[reg] = ('address', '|'.join(sorted({v[1] for v in values})),
                                  frozenset().union(*(v[2] for v in values)))
                elif all(v == values[0] for v in values): state[reg] = values[0]
                else: state[reg] = WORD
        for ins in block.instructions: _apply(state, ins)
        outputs[key] = state
        if block.terminator and block.terminator.opcode == 'jr':
            value = state.get('v0', WORD)
            if value[0] != 'address': return decline('a return after its delay slot is scalar or unresolved')
            witnesses.append({'return_instruction':block.terminator.index,
                'delay_instruction':block.delay_slot.index, 'address_base':value[1],
                'address_instructions':sorted(value[2]),
                'instructions':[graph.instructions[i].text for i in sorted(value[2])]})
    if not witnesses: return decline('no witnessed returns')
    new = 'void *'+own[len('s32 '):]
    child = {**context, 'own_prototype':new,
             'declarations':context['declarations'].replace(own,new,1)}
    return {**report, 'status':'proposed', 'context':child, 'witnesses':witnesses,
            'context_argument_types_are_hypotheses':True}
