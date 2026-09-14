"""Conservative fixed-point symbolic dataflow over :mod:`solver.cfg`.

This is a must-analysis: a register or stack slot remains known at a join only
when every observed predecessor proves the same value.  Unlike the old linear
walk, a branch does not erase facts that are invariant across both paths, and
loops converge through a worklist rather than relying on instruction order.

The values are identities, not inferred C types.  ``load(param0+0x10)`` means
"the value read from this mechanically identified address"; it does not claim
that the value is a pointer or name a struct field.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field

from solver import cfg


CALLER_SAVED = frozenset({
    "at", "v0", "v1", "a0", "a1", "a2", "a3",
    "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7", "t8", "t9",
    "ra",
})

LOAD_WIDTH = {
    "lb": 1, "lbu": 1, "lh": 2, "lhu": 2, "lw": 4, "lwl": 4,
    "lwr": 4, "lwc1": 4, "ld": 8, "ldc1": 8,
}
STORE_WIDTH = {
    "sb": 1, "sh": 2, "sw": 4, "swl": 4, "swr": 4,
    "swc1": 4, "sd": 8, "sdc1": 8,
}
WRITES_FIRST = frozenset({
    *LOAD_WIDTH,
    "move", "li", "la", "lui", "add", "addu", "addi", "addiu",
    "sub", "subu", "and", "andi", "or", "ori", "xor", "xori", "nor",
    "sll", "srl", "sra", "sllv", "srlv", "srav", "slt", "slti",
    "sltu", "sltiu", "mul", "mfhi", "mflo", "neg", "negu", "not",
})

MEMORY = re.compile(
    r"^(?P<value>\$?[\w.]+)\s*,\s*"
    r"(?P<offset>-?(?:0x)?[0-9A-Fa-f]+)\(\$?(?P<base>\w+)\)$")
RELOC_MEMORY = re.compile(
    r"^(?P<value>\$?[\w.]+)\s*,\s*%lo\("
    r"(?P<symbol>[A-Za-z_.$][\w.$]*)(?:\s*\+\s*(?P<addend>-?(?:0x)?[0-9A-Fa-f]+))?"
    r"\)\(\$?(?P<base>\w+)\)$")
HI_RELOC = re.compile(r"%hi\(([A-Za-z_.$][\w.$]*)\)")
LO_RELOC = re.compile(r"%lo\(([A-Za-z_.$][\w.$]*)\)")


def reg(text: str) -> str:
    return text.strip().lstrip("$")


def number(text: str) -> int | None:
    try:
        return int(text, 0)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Value:
    kind: str
    name: str = ""
    offset: int = 0
    inner: "Value | None" = None
    site: int | None = None
    width: int | None = None
    signed: bool | None = None

    @staticmethod
    def address(name: str, offset: int = 0) -> "Value":
        return Value("address", name, offset)

    @staticmethod
    def constant(value: int) -> "Value":
        return Value("constant", offset=value)

    @staticmethod
    def loaded(address: "Value", *, width: int | None = None,
               signed: bool | None = None) -> "Value":
        return Value("load", inner=address, width=width, signed=signed)

    @staticmethod
    def call_result(name: str, site: int) -> "Value":
        return Value("call_result", name=name, site=site)

    def plus(self, amount: int) -> "Value | None":
        if self.kind == "address":
            return Value.address(self.name, self.offset + amount)
        if self.kind == "constant":
            return Value.constant(self.offset + amount)
        if self.kind == "load":
            return Value("load", offset=self.offset + amount, inner=self.inner,
                         width=self.width, signed=self.signed)
        if self.kind == "call_result":
            return Value("call_result", self.name, self.offset + amount,
                         site=self.site)
        return None

    def comes_from_call(self, site: int) -> bool:
        if self.kind == "call_result" and self.site == site:
            return True
        return self.inner.comes_from_call(site) if self.inner is not None else False

    def describe(self, *, precise: bool = False) -> str:
        if self.kind == "address":
            if not self.offset:
                return self.name
            sign = "+" if self.offset >= 0 else "-"
            return f"{self.name}{sign}{abs(self.offset):#x}"
        if self.kind == "constant":
            return f"{self.offset:#x}"
        if self.kind == "load" and self.inner is not None:
            if precise and self.width is not None:
                sign = ("s" if self.signed is True else
                        "u" if self.signed is False else "")
                base = (f"load_{sign}{self.width * 8}"
                        f"(byte_address({self.inner.describe(precise=True)}))")
            else:
                base = f"load({self.inner.describe(precise=precise)})"
            if not self.offset:
                return base
            sign = "+" if self.offset >= 0 else "-"
            return f"{base}{sign}{abs(self.offset):#x}"
        if self.kind == "call_result":
            base = f"ret({self.name}@i{self.site})"
            if not self.offset:
                return base
            sign = "+" if self.offset >= 0 else "-"
            return f"{base}{sign}{abs(self.offset):#x}"
        return "unknown"


@dataclass
class State:
    registers: dict[str, Value] = field(default_factory=dict)
    stack: dict[int, Value] = field(default_factory=dict)

    def copy(self) -> "State":
        return State(dict(self.registers), dict(self.stack))


@dataclass(frozen=True)
class MemoryAccess:
    instruction: int
    opcode: str
    address: Value | None
    width: int
    is_load: bool

    @property
    def resolved(self) -> bool:
        return self.address is not None


@dataclass(frozen=True)
class CallSite:
    instruction: int
    opcode: str
    target: str | None
    target_value: Value | None
    arguments: tuple[Value | None, Value | None, Value | None, Value | None]
    result: Value
    delay_slot: int | None


@dataclass
class Result:
    graph: cfg.ControlFlowGraph
    block_in: dict[int, State]
    block_out: dict[int, State]
    instruction_in: dict[int, State]
    accesses: dict[int, MemoryAccess]
    callsites: dict[int, CallSite]


def entry_state() -> State:
    registers = {f"a{i}": Value.address(f"param{i}") for i in range(4)}
    registers["sp"] = Value.address("stack")
    registers["gp"] = Value.address("gp")
    return State(registers, {})


def merge(states: list[State]) -> State:
    """Meet operation for facts true on every incoming path."""
    if not states:
        return State()
    common_regs = dict(states[0].registers)
    common_stack = dict(states[0].stack)
    for state in states[1:]:
        common_regs = {key: value for key, value in common_regs.items()
                       if state.registers.get(key) == value}
        common_stack = {key: value for key, value in common_stack.items()
                        if state.stack.get(key) == value}
    return State(common_regs, common_stack)


def _address(state: State, insn: cfg.Instruction) -> Value | None:
    rest = ", ".join(insn.operands)
    reloc = RELOC_MEMORY.match(rest)
    if reloc:
        addend = number(reloc.group("addend")) or 0
        return Value.address(reloc.group("symbol"), addend)
    memory = MEMORY.match(rest)
    if not memory:
        return None
    base = state.registers.get(reg(memory.group("base")))
    offset = number(memory.group("offset"))
    if base is None or offset is None:
        return None
    return base.plus(offset)


def _destination(insn: cfg.Instruction) -> str | None:
    if insn.opcode not in WRITES_FIRST or not insn.operands:
        return None
    return reg(insn.operands[0])


def _clobber_call(state: State, result: Value | None = None) -> None:
    for name in CALLER_SAVED:
        state.registers.pop(name, None)
    if result is not None:
        # The ABI says the call defines v0 even when the eventual exact
        # prototype turns out to be void.  Keeping a stable result identity
        # lets an interprocedural consumer trace real uses; a void contract can
        # then reject impossible uses rather than the dataflow guessing here.
        state.registers["v0"] = result


def _call_target(insn: cfg.Instruction, state: State) -> tuple[str | None,
                                                               Value | None]:
    if not insn.operands:
        return None, None
    if insn.opcode in {"jal", "bal"}:
        return insn.operands[-1].lstrip("$"), None
    if insn.opcode == "jalr":
        target_reg = reg(insn.operands[-1])
        target_value = state.registers.get(target_reg)
        return None, target_value
    return None, None


def _apply(state: State, insn: cfg.Instruction,
           accesses: dict[int, MemoryAccess] | None = None) -> None:
    op = insn.opcode
    ops = insn.operands

    if op in LOAD_WIDTH | STORE_WIDTH:
        address = _address(state, insn)
        is_load = op in LOAD_WIDTH
        if accesses is not None:
            accesses[insn.index] = MemoryAccess(
                insn.index, op, address,
                LOAD_WIDTH.get(op, STORE_WIDTH.get(op, 0)), is_load)
        if not ops:
            return
        value_reg = reg(ops[0])
        if is_load:
            value = None
            if address is not None and address.kind == "address" \
                    and address.name == "stack":
                value = state.stack.get(address.offset)
            if value is None and address is not None:
                signed = (True if op in {"lb", "lh"} else
                          False if op in {"lbu", "lhu"} else None)
                value = Value.loaded(address, width=LOAD_WIDTH[op],
                                     signed=signed)
            if value is None:
                state.registers.pop(value_reg, None)
            else:
                state.registers[value_reg] = value
        elif address is not None and address.kind == "address" \
                and address.name == "stack":
            value = state.registers.get(value_reg)
            if value is None:
                state.stack.pop(address.offset, None)
            else:
                state.stack[address.offset] = value
        return

    dest = _destination(insn)
    if op == "move" and len(ops) >= 2:
        value = state.registers.get(reg(ops[1]))
        if value is None:
            state.registers.pop(reg(ops[0]), None)
        else:
            state.registers[reg(ops[0])] = value
        return
    if op in {"or", "addu"} and len(ops) >= 3:
        left, right = reg(ops[1]), reg(ops[2])
        source = right if left == "zero" else left if right == "zero" else None
        if source is not None:
            value = (Value.constant(0) if source == "zero"
                     else state.registers.get(source))
            if value is None:
                state.registers.pop(reg(ops[0]), None)
            else:
                state.registers[reg(ops[0])] = value
            return
    if op in {"addiu", "addi"} and len(ops) >= 3:
        target, source = reg(ops[0]), reg(ops[1])
        reloc = LO_RELOC.search(ops[2])
        if reloc:
            state.registers[target] = Value.address(reloc.group(1))
            return
        amount = number(ops[2])
        base = state.registers.get(source)
        value = base.plus(amount) if base is not None and amount is not None else None
        if value is None:
            state.registers.pop(target, None)
        else:
            state.registers[target] = value
        return
    if op == "lui" and len(ops) >= 2:
        target = reg(ops[0])
        reloc = HI_RELOC.search(ops[1])
        amount = number(ops[1])
        if reloc:
            state.registers[target] = Value.address(reloc.group(1))
        elif amount is not None:
            state.registers[target] = Value.constant((amount & 0xFFFF) << 16)
        else:
            state.registers.pop(target, None)
        return
    if op in {"li", "la"} and len(ops) >= 2:
        target = reg(ops[0])
        amount = number(ops[1])
        if op == "li" and amount is not None:
            state.registers[target] = Value.constant(amount)
        elif op == "la":
            state.registers[target] = Value.address(ops[1])
        else:
            state.registers.pop(target, None)
        return
    if op == "ori" and len(ops) >= 3:
        target, source = reg(ops[0]), reg(ops[1])
        base, amount = state.registers.get(source), number(ops[2])
        if base is not None and base.kind == "constant" and amount is not None:
            state.registers[target] = Value.constant(base.offset | amount)
            return

    if dest is not None and dest != "zero":
        state.registers.pop(dest, None)


def _transfer(block: cfg.BasicBlock, incoming: State,
              accesses: dict[int, MemoryAccess] | None = None,
              instruction_in: dict[int, State] | None = None,
              callsites: dict[int, CallSite] | None = None) -> State:
    state = incoming.copy()
    pending_call: tuple[cfg.Instruction, str | None, Value | None] | None = None
    likely_before_delay: State | None = None
    for insn in block.instructions:
        if instruction_in is not None:
            instruction_in[insn.index] = state.copy()
        was_pending = pending_call
        pending_call = None
        if block.terminator is not None and block.terminator.branch_likely \
                and insn.index == block.terminator.index:
            likely_before_delay = state.copy()
        _apply(state, insn, accesses)
        if was_pending is not None:
            call, target, target_value = was_pending
            label = target or (target_value.describe()
                               if target_value is not None else "indirect")
            result = Value.call_result(label, call.index)
            if callsites is not None:
                callsites[call.index] = CallSite(
                    instruction=call.index,
                    opcode=call.opcode,
                    target=target,
                    target_value=target_value,
                    arguments=tuple(state.registers.get(f"a{i}")
                                    for i in range(4)),
                    result=result,
                    delay_slot=insn.index,
                )
            _clobber_call(state, result)
        if insn.opcode in cfg.CALL_OPS:
            target, target_value = _call_target(insn, state)
            pending_call = (insn, target, target_value)
    if pending_call is not None:
        call, target, target_value = pending_call
        label = target or (target_value.describe()
                           if target_value is not None else "indirect")
        result = Value.call_result(label, call.index)
        if callsites is not None:
            callsites[call.index] = CallSite(
                instruction=call.index,
                opcode=call.opcode,
                target=target,
                target_value=target_value,
                arguments=tuple(state.registers.get(f"a{i}") for i in range(4)),
                result=result,
                delay_slot=None,
            )
        _clobber_call(state, result)
    if likely_before_delay is not None and block.delay_slot is not None:
        # A likely delay slot executes only on the taken edge.  Until edge
        # states are represented separately, retain only facts valid whether
        # it executed or was annulled.
        state = merge([likely_before_delay, state])
    return state


def analyse(asm: str) -> Result:
    graph = cfg.build(asm)
    if graph.entry is None:
        return Result(graph, {}, {}, {}, {}, {})

    block_in: dict[int, State] = {graph.entry: entry_state()}
    block_out: dict[int, State] = {}
    queue = deque([graph.entry])
    queued = {graph.entry}

    while queue:
        block_id = queue.popleft()
        queued.discard(block_id)
        incoming = block_in[block_id]
        outgoing = _transfer(graph.blocks[block_id], incoming)
        if block_out.get(block_id) == outgoing:
            continue
        block_out[block_id] = outgoing
        for succ in sorted(graph.blocks[block_id].successors):
            pred_states = [block_out[p] for p in graph.blocks[succ].predecessors
                           if p in block_out]
            update = merge(pred_states)
            if block_in.get(succ) != update:
                block_in[succ] = update
                if succ not in queued:
                    queue.append(succ)
                    queued.add(succ)

    instruction_in: dict[int, State] = {}
    accesses: dict[int, MemoryAccess] = {}
    callsites: dict[int, CallSite] = {}
    for block_id in graph.reverse_postorder():
        if block_id in block_in:
            _transfer(graph.blocks[block_id], block_in[block_id], accesses,
                      instruction_in, callsites)
    return Result(graph, block_in, block_out, instruction_in, accesses,
                  callsites)
