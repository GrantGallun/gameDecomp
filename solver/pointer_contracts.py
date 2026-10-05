"""Per-argument pointer contracts for opaque callees, derived from their ROM-verified extracted assembly.

Why: an opaque call's identity in the differential checker includes its argument
labels, and a pointer into the caller's frame is labelled by its address
(`stack+0xfd4`). Two functions that differ only in frame layout therefore
"fail" at the first call that receives a local buffer, and the synthetic return
values and clobbers derived from those labels cascade. On 2026-09-14, 66 of 263
semantic-lane nodes failed only this way, mostly through makeFixedRotation* and
transformVec3iByFixedMatrix. Equating stack addresses would hide different buffer
contents, so this module derives, from the callee's own instructions, exactly
which bytes it reads and writes through each pointer argument:

  read       stack label becomes the digest of those bytes at call time
  write      stack label becomes an extent; the bytes are written with a payload
             derived from the call's other labels
  readwrite  both

An argument is disqualified (no contract; a stack address passed there stays
unsupported, as before) when it is used as a value (arithmetic, comparison,
branch), passed to a nested call within that call's arity, stored anywhere but
the callee's own stack slots, returned, accessed at a negative offset, or written
by a store that a branch can skip. The whole callee declines on backward branches,
indirect jumps, stack-pointer rewrites or instruction forms outside this dialect.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import re

from solver import cfg, mips_differential as d

LOADS = {"lb": 1, "lbu": 1, "lh": 2, "lhu": 2, "lw": 4, "lwu": 4, "lwc1": 4, "ldc1": 8}
STORES = {"sb": 1, "sh": 2, "sw": 4, "swc1": 4, "sdc1": 8}
NO_DEST = {"mult", "multu", "div", "divu", "dmult", "dmultu", "ddiv", "ddivu", "mthi", "mtlo", "nop",
           "syscall", "break", "teq", "tne"}
CALLER_SAVED = {"at", "v0", "v1", "a0", "a1", "a2", "a3", "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
                "t8", "t9", "ra"}
MEMORY = re.compile(r"^(?P<off>-?(?:0[xX][0-9a-fA-F]+|\d+)|%lo\([^)]*\))?\((?P<base>\$?\w+)\)$")
RELOCATED = -1 << 40                                   # sentinel: a link-time %lo offset
NUMBER = re.compile(r"^-?(?:0[xX][0-9a-fA-F]+|\d+)$")


class Declined(ValueError):
    pass


def _reg(operand: str) -> str:
    name = operand.strip().lstrip("$")
    return "zero" if name in ("0", "zero") else name


def _memory(operand: str) -> tuple[int, str]:
    match = MEMORY.match(operand.strip())
    if not match:
        raise Declined(f"unsupported memory operand {operand}")
    offset = match.group("off") or "0"
    return (RELOCATED if offset.startswith("%lo") else int(offset, 0)), _reg(match.group("base"))


@dataclass(frozen=True)
class ArgumentContract:
    index: int
    reads: tuple[int, ...]
    writes: tuple[int, ...]

    @property
    def kind(self) -> str:
        return "readwrite" if self.reads and self.writes else "read" if self.reads else "write"

    @property
    def extent(self) -> int:
        return max(self.reads + self.writes) + 1


def analyze(program: d.Program, arity: int, nested_arity=None) -> tuple[ArgumentContract, ...]:
    """Byte offsets read and written through each qualifying pointer argument.

    `nested_arity(name)` gives a nested callee's argument count (None: assume 4).
    """
    instructions = program.instructions
    if not 0 < arity <= 4:
        raise Declined("pointer contracts cover register arguments a0..a3 only")
    reads = {k: set() for k in range(arity)}
    writes = {k: set() for k in range(arity)}
    store_sites: dict[int, set] = defaultdict(set)
    disqualified: dict[int, str] = {}
    skips: list[tuple[int, int]] = []
    incoming: dict[int, list[tuple[dict, int]]] = defaultdict(list)

    def disqualify(args, reason):
        for arg in args:
            disqualified.setdefault(arg, reason)

    def merge(states):
        merged, deltas = {}, {delta for _state, delta in states}
        if len(deltas) != 1:
            raise Declined("stack pointer delta differs across paths")
        for state, _delta in states:
            for location, args in state.items():
                merged[location] = merged.get(location, frozenset()) | args
        return merged, deltas.pop()

    def held(state, register):
        return state.get(register, frozenset()) if register != "zero" else frozenset()

    def assign(state, register, args):
        if register == "sp":
            raise Declined("stack pointer rewritten")
        if register == "zero":
            return
        if args:
            state[register] = frozenset(args)
        else:
            state.pop(register, None)

    def step(index, state, delta):
        instruction = instructions[index]
        op, operands = instruction.opcode, instruction.operands
        if op in LOADS:
            destination = _reg(operands[0])
            offset, base = _memory(operands[1])
            if base == "sp":
                slot = state.get(("slot", delta + offset), frozenset()) if LOADS[op] == 4 else frozenset()
                if LOADS[op] != 4 and state.get(("slot", delta + offset)):
                    disqualify(state[("slot", delta + offset)], "partial load of a saved pointer")
                assign(state, destination, slot)
                return delta
            args = held(state, base)
            if offset < 0:
                disqualify(args, "negative or relocated pointer offset")
            else:
                for arg in args:
                    reads[arg].update(range(offset, offset + LOADS[op]))
            assign(state, destination, frozenset())
            return delta
        if op in STORES:
            value = _reg(operands[0])
            offset, base = _memory(operands[1])
            value_args = held(state, value)
            if base == "sp":
                if offset == RELOCATED:
                    raise Declined("relocated stack offset")
                for shift in range(-3, STORES[op]):
                    state.pop(("slot", delta + offset + shift), None)
                if value_args:
                    if STORES[op] != 4:
                        disqualify(value_args, "partial pointer store")
                    else:
                        state[("slot", delta + offset)] = value_args
                return delta
            disqualify(value_args, "pointer escapes into memory")
            args = held(state, base)
            if offset < 0:
                disqualify(args, "negative or relocated pointer offset")
            else:
                for arg in args:
                    writes[arg].update(range(offset, offset + STORES[op]))
                    store_sites[index].add(arg)
            return delta
        if op == "addiu" and _reg(operands[0]) == "sp" and _reg(operands[1]) == "sp":
            return delta + int(operands[2], 0)
        registers = [_reg(o) for o in operands if re.fullmatch(r"\$?\w+", o.strip()) and not NUMBER.match(o.strip())]
        if op == "move" or (op in ("or", "addu", "daddu") and len(registers) == 3 and "zero" in registers[1:]):
            source = registers[1] if op == "move" or registers[2] == "zero" else registers[2]
            assign(state, registers[0], held(state, source))
            return delta
        if op in NO_DEST:
            for register in registers:
                disqualify(held(state, register), f"pointer used as a value in {op}")
            return delta
        if not registers:
            raise Declined(f"unsupported instruction form: {instruction.text}")
        destination, uses = registers[0], ([] if op == "lui" else registers[1:])
        for register in uses:
            disqualify(held(state, register), f"pointer arithmetic or comparison in {op}")
        assign(state, destination, frozenset())
        return delta

    state = {f"a{k}": frozenset({k}) for k in range(arity)}
    delta, live, index = 0, True, 0
    while index < len(instructions):
        if incoming.get(index):
            states = incoming.pop(index) + ([(state, delta)] if live else [])
            merged, delta = merge(states)
            state, live = dict(merged), True
        if not live:
            index += 1
            continue
        instruction = instructions[index]
        op = instruction.opcode
        is_branch = op in ("b",) or cfg.is_conditional_branch(op)
        if op in ("jal", "jalr", "jr", "j") or is_branch:
            if op in ("jalr", "j") or (op == "jr" and _reg(instruction.operands[0]) != "ra"):
                raise Declined(f"indirect or non-local jump: {instruction.text}")
            if is_branch and op != "b":
                for operand in instruction.operands[:-1]:
                    disqualify(held(state, _reg(operand)), "branch on a pointer")
            if index + 1 < len(instructions):
                delta = step(index + 1, state, delta)        # the delay slot executes before the transfer
            if op == "jal":
                count = nested_arity(instruction.operands[0]) if nested_arity else None
                for k in range(4 if count is None else min(count, 4)):
                    disqualify(held(state, f"a{k}"), f"pointer passed to {instruction.operands[0]}")
                for register in CALLER_SAVED:
                    state.pop(register, None)
                index += 2
                continue
            if op == "jr":
                disqualify(held(state, "v0") | held(state, "v1"), "pointer returned")
                live, index = False, index + 2
                continue
            target = program.branch_target(instruction.operands[-1])
            if target <= index + 1:
                raise Declined("backward branch or loop")
            skips.append((index, target))
            incoming[target].append((dict(state), delta))
            live = op != "b"
            index += 2
            continue
        delta = step(index, state, delta)
        index += 1
    for site, args in store_sites.items():
        if any(start < site < end for start, end in skips):
            disqualify(args, "a branch can skip a store through this argument")
    contracts = []
    for arg in range(arity):
        if arg in disqualified or not (reads[arg] or writes[arg]):
            continue
        if max(reads[arg] | writes[arg]) >= 4096:
            continue
        contracts.append(ArgumentContract(arg, tuple(sorted(reads[arg])), tuple(sorted(writes[arg]))))
    if not contracts:
        raise Declined("no qualifying pointer argument: " + "; ".join(f"a{k}: {v}" for k, v in sorted(disqualified.items())))
    return tuple(contracts)


@dataclass(frozen=True)
class PointerContract:
    """Environment effect with the OutputBuffer interface, bound to one callee's instructions."""
    arguments: tuple[ArgumentContract, ...]
    provenance: str
    identity: str

    def labels(self, runner, raw, labels):
        contracted = {c.index: c for c in self.arguments}
        labels = list(labels)
        spans = []
        for index, value in enumerate(raw):
            if d.STACK_BASE <= value < d.STACK_BASE + d.STACK_SIZE and index not in contracted:
                raise d.UnsupportedInstruction("pointer contract cannot discharge an uncontracted stack argument")
        for contract in self.arguments:
            if contract.index >= len(raw):
                raise d.UnsupportedInstruction("contracted pointer argument exceeds verified arity")
            address = raw[contract.index]
            if not d.STACK_BASE <= address < d.STACK_BASE + d.STACK_SIZE:
                continue                               # persistent objects keep address identity
            if not runner.get("sp") <= address or address + contract.extent > runner.initial_reg["sp"]:
                raise d.UnsupportedInstruction("contracted stack pointer outside the active caller frame")
            spans.append((address, address + contract.extent))
            label = f"stack-{contract.kind}[{contract.extent}]"
            if contract.reads:
                content = bytes(runner.memory.read(address + offset, 1) for offset in contract.reads)
                label += ":" + hashlib.sha256(content).hexdigest()[:16]
            labels[contract.index] = label
        spans.sort()
        if any(left_end > right_start for (_s, left_end), (right_start, _e) in zip(spans, spans[1:])):
            raise d.UnsupportedInstruction("aliased contracted stack arguments")
        return labels

    def apply(self, runner, raw, arguments, ordinal, returned, instruction):
        for contract in self.arguments:
            address = raw[contract.index]
            if not d.STACK_BASE <= address < d.STACK_BASE + d.STACK_SIZE:
                continue
            for offset in contract.writes:
                byte = d._stable_word("pointer-contract-output", runner.case_seed, ordinal, *arguments,
                                      contract.index, offset) & 255
                runner.memory.write(address + offset, 1, byte)
                runner._record_write(instruction, address + offset, 1, byte,
                                     f"contract output argument {contract.index}+{offset}",
                                     f"pointer contract: {self.provenance}")


def contract_for(name: str, assembly: str, arity: int, provenance: str, nested_arity=None) -> PointerContract:
    program = d.Program.parse(name, assembly)
    return PointerContract(analyze(program, arity, nested_arity), provenance,
                           hashlib.sha256(assembly.encode()).hexdigest())


_VERIFIED: dict[tuple[str, str], tuple[str, str] | Declined] = {}


def verified_assembly(repo, name: str) -> tuple[str, str]:
    """Cached per process: every semantic panel of a job would otherwise reassemble the same callees."""
    key = (str(repo), name)
    if key not in _VERIFIED:
        try:
            _VERIFIED[key] = _verified_assembly(repo, name)
        except Declined as error:
            _VERIFIED[key] = error
    value = _VERIFIED[key]
    if isinstance(value, Declined):
        raise value
    return value


def _verified_assembly(repo, name: str) -> tuple[str, str]:
    """The callee's extracted instructions, bound to ROM bytes and reassembled with relocations masked."""
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path
    import yaml
    from solver import sdk_intake
    matches = []
    for path in sorted((repo / "asm").rglob(f"{name}.s")):
        text = path.read_text(errors="replace")
        labels = re.findall(r"(?m)^\s*glabel\s+(\w+)\s*$", text)
        if labels == [name]:
            matches.append((path, text))
    if len(matches) != 1:
        raise Declined("missing or ambiguous single-symbol extracted assembly")
    path, text = matches[0]
    start = text.index("glabel " + name)
    stop = text.find("endlabel " + name, start)
    if stop < 0:
        raise Declined("extracted assembly has no endlabel")
    body = text[start:stop]
    program = d.Program.parse(name, body)
    words = re.findall(r"/\*\s*([0-9A-Fa-f]+)\s+([0-9A-Fa-f]{8})\s+([0-9A-Fa-f]{8})\s*\*/", body)
    if not words or len(words) != len(program.instructions) or len(words) > 1024:
        raise Declined("instruction annotations do not cover the extracted body")
    config = yaml.safe_load((repo / "snowboardkids.yaml").read_text())
    rom = (repo / config["options"]["target_path"]).read_bytes()
    if hashlib.sha1(rom).hexdigest() != config["sha1"]:
        raise Declined("ROM identity differs from extraction configuration")
    first_offset, first_pc = int(words[0][0], 16), int(words[0][1], 16)
    if sdk_intake.rom_offset(config, first_pc, len(words) * 4, len(rom)) != first_offset:
        raise Declined("callee ROM range disagrees with extraction mapping")
    for i, (offset, pc, word) in enumerate(words):
        if (int(offset, 16), int(pc, 16)) != (first_offset + 4 * i, first_pc + 4 * i):
            raise Declined("non-contiguous instruction annotations")
        if rom[int(offset, 16):int(offset, 16) + 4] != bytes.fromhex(word):
            raise Declined("callee instruction bytes disagree with ROM")
    assembler, objcopy = shutil.which("mips-linux-gnu-as"), shutil.which("mips-linux-gnu-objcopy")
    if not assembler or not objcopy:
        raise Declined("MIPS assembler/objcopy unavailable for callee binding")
    registers = r"(?<![\w$])(" + "|".join(sorted(d.REGISTER_NAMES)) + r")(?!\w)"
    lines, masks = [".set noreorder", ".set noat", ".text"], []
    for instruction in program.instructions:
        operands, mask = list(instruction.operands), 0xFFFFFFFF
        if instruction.opcode == "jal":
            operands, mask = ["0"], 0xFC000000                 # call target identity is not part of the contract
        elif instruction.opcode in ("b",) or cfg.is_conditional_branch(instruction.opcode):
            operands[-1] = f".Lcontract_{program.branch_target(operands[-1])}"
        if any("%hi(" in o or "%lo(" in o for o in operands):
            operands = [re.sub(r"%(?:hi|lo)\([^)]*\)", "0", o) for o in operands]
            mask = 0xFFFF0000
        rendered = re.sub(registers, lambda m: "$" + m[1], ",".join(operands).replace("$", ""))
        lines += [f".Lcontract_{instruction.index}:", f"{instruction.opcode} {rendered}".strip()]
        masks.append(mask)
    with tempfile.TemporaryDirectory(prefix="pointer-contract-") as temporary:
        obj, binary = Path(temporary) / "callee.o", Path(temporary) / "text.bin"
        result = subprocess.run([assembler, "-EB", "-mabi=32", "-march=vr4300", "-o", str(obj), "-"],
                                input="\n".join(lines) + "\n", text=True, capture_output=True, timeout=20)
        if result.returncode:
            raise Declined("callee reassembly failed: " + result.stderr[-300:])
        subprocess.run([objcopy, "-O", "binary", "--only-section=.text", str(obj), str(binary)],
                       check=True, capture_output=True, timeout=20)
        assembled = binary.read_bytes()
    for i, (_offset, _pc, word) in enumerate(words):
        mine = int.from_bytes(assembled[i * 4:i * 4 + 4], "big")
        if (mine & masks[i]) != (int(word, 16) & masks[i]):
            raise Declined(f"parsed instruction {i} does not reassemble to its ROM word")
    return body, "ROM-verified extracted assembly: " + str(path.relative_to(repo))


def extend_environment(repo, environment, callees, arities, nested_arity=None):
    """Add pointer contracts for opaque callees; returns one admission row per callee considered."""
    rows = []
    for name in sorted(set(callees)):
        if name in environment.leaves or name in environment.outputs:
            continue
        arity = arities.get(name)
        if not isinstance(arity, int) or arity <= 0:
            rows.append({"callee": name, "status": "opaque", "reason": "pointer contract needs a known arity"})
            continue
        try:
            body, provenance = verified_assembly(repo, name)
            contract = contract_for(name, body, min(arity, 4), provenance, nested_arity)
        except (Declined, d.UnsupportedInstruction, OSError, ValueError, KeyError) as error:
            # A callee the interpreter cannot parse (2026-09-14: a branch to `.L8007210C` outside its body)
            # stays opaque; raising here parked five callers' revalidate jobs.
            rows.append({"callee": name, "status": "opaque", "reason": f"pointer contract: {error}"})
            continue
        environment.outputs[name] = contract
        rows.append({"callee": name, "status": "pointer-contract", "identity": contract.identity,
                     "arguments": [{"index": c.index, "kind": c.kind, "extent": c.extent} for c in contract.arguments],
                     "authority": provenance})
    return rows
