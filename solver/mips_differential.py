"""Deterministic, instrumented MIPS function execution for semantic checks.

This is deliberately a function runner rather than a console emulator.  It
executes the small MIPS instruction subset present in isolated decompilation
workspaces, hooks external calls, and compares caller-visible behavior.  The
candidate is compiled to MIPS before it reaches this module, so both sides use
the same integer width, endianness, ABI, and branch-delay semantics.

Passing finitely many cases is behavioral evidence, not a proof.  Unsupported
instructions and target-side memory faults are inconclusive; they never become
an accidental pass.  Byte exactness remains a separate terminal oracle.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import math
import random
import re
import struct
from collections import deque
from dataclasses import asdict, dataclass, field, replace

from solver import cfg


MASK32 = 0xFFFFFFFF
COVERAGE_MODEL_VERSION = 2  # Includes switch successors and unresolved jr debt.
COMPLETED_STATUSES = frozenset({"returned", "nonreturn"})
PROGRAM_BASE = 0x80000000
RETURN_SENTINEL = 0xFFFFFFFC
PLAYER_BASE = 0x10000000
ARG_POINTER_BASES = {
    "a0": PLAYER_BASE,
    "a1": 0x11000000,
    "a2": 0x12000000,
    "a3": 0x13000000,
}
STACK_ARG_POINTER_BASES = {index: 0x14000000 + (index - 4) * 0x100000
                           for index in range(4, 16)}
STACK_BASE = 0x20000000
STACK_SIZE = 0x2000
INITIAL_SP = STACK_BASE + 0x1000

CALLER_SAVED = (
    "at", "v0", "v1", "a0", "a1", "a2", "a3",
    "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7", "t8", "t9",
)
CALLEE_SAVED = (
    "sp", "fp", "ra", "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7",
)
FLOAT_REGISTERS = tuple(f"f{index}" for index in range(32))
REGISTER_NAMES = frozenset(
    ("zero",) + CALLER_SAVED + CALLEE_SAVED +
    ("gp", "c1_fcsr") + FLOAT_REGISTERS)

SYMBOL_REGION_SIZES = {
    # fixedSine indexes a signed-halfword lookup table with a 12-bit angle.
    # The symbol stride leaves 64 KiB available, but allocating only the old
    # 256-byte default turned valid table indexes into harness memory faults.
    "gSineTable": 0x2000,
    # Race code treats this symbol as an array of large player records.  For
    # example, updateRacePickupIdle reaches fields beyond +0x568 after adding
    # a per-player stride.  The old scalar-sized fallback prevented the target
    # from entering otherwise valid paths, so coverage stopped at its pause
    # guard before candidate semantics could even be assessed.
    "gRacePlayers": 0x2000,
}
SYMBOL_REGION_STRIDE = 0x10000

MODE16_CALL_ARITIES = {
    "setRaceMotionAnimation": 2,
    "resetRacePlayerTrickSubstate": 1,
    "stepRaceMotionAnimationUntilEnd": 1,
    "updateRacePlayerTrickSubstate": 1,
    "updateRacePlayerLeanAngle": 3,
    "clampRacePlayerVectorXZSpeed": 2,
    "fixedSine": 1,
    "createCallbackTaskWithUserIdPreservingArgs": 4,
}


class UnsupportedInstruction(RuntimeError):
    pass


class MemoryFault(RuntimeError):
    pass


def u32(value: int) -> int:
    return value & MASK32


def s32(value: int) -> int:
    value &= MASK32
    return value - 0x100000000 if value & 0x80000000 else value


def sign_extend(value: int, bits: int) -> int:
    mask = (1 << bits) - 1
    value &= mask
    sign = 1 << (bits - 1)
    return value - (1 << bits) if value & sign else value


def _float32_from_bits(value: int) -> float:
    return struct.unpack(">f", struct.pack(">I", u32(value)))[0]


def _float32_bits(value: float) -> int:
    try:
        return struct.unpack(">I", struct.pack(">f", value))[0]
    except OverflowError as exc:
        # FCSR rounding/trap behavior is not modeled. Keep this execution
        # inconclusive instead of crashing the campaign or inventing a result.
        raise UnsupportedInstruction(
            "float32 overflow requires unmodeled FCSR rounding/trap behavior") from exc


def _float64_from_words(high: int, low: int) -> float:
    return struct.unpack(">d", struct.pack(">II", u32(high), u32(low)))[0]


def _float64_words(value: float) -> tuple[int, int]:
    return struct.unpack(">II", struct.pack(">d", value))


_SYMBOL_EXPRESSION = re.compile(
    r"^([A-Za-z_.$][\w.$]*)(?:([+-])((?:0x)?[0-9a-fA-F]+))?$")


def _parse_symbol_expression(text: str) -> tuple[str, int]:
    match = _SYMBOL_EXPRESSION.fullmatch(text.strip())
    if match is None:
        raise UnsupportedInstruction(
            f"unsupported relocation expression {text!r}")
    name, sign, addend_text = match.groups()
    if addend_text is None:
        return name, 0
    addend = int(addend_text, 0)
    return name, -addend if sign == "-" else addend


@dataclass(frozen=True)
class Program:
    name: str
    instructions: tuple[cfg.Instruction, ...]
    labels: dict[str, int]
    # (data symbol, byte offset) -> function-relative text byte offset.
    # Populated from workspace ELF R_MIPS_32 annotations for switch tables.
    data_words: dict[tuple[str, int], int] = field(default_factory=dict)
    # Linker addresses preserve physical aliasing between distinct symbol
    # names and base-plus-offset expressions.
    symbol_addresses: dict[str, int] = field(default_factory=dict)
    # Initialized object bytes (usually rodata) recovered mechanically from
    # the target/candidate ELF, keyed by relocation symbol and byte offset.
    data_bytes: dict[tuple[str, int], int] = field(default_factory=dict)
    text_base: int = PROGRAM_BASE
    memory_extents: dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        if (type(self.text_base) is not int or self.text_base % 4
                or self.text_base < 0 or self.text_base + 4*len(self.instructions) > 0x100000000):
            raise ValueError('invalid program code-address range')

    @classmethod
    def parse(cls, name: str, assembly: str) -> "Program":
        instructions, labels = cfg.parse_assembly(assembly)
        if not instructions:
            raise ValueError(f"{name} contains no instructions")
        data_words: dict[tuple[str, int], int] = {}
        for symbol, offset, text_offset in re.findall(
                r"(?m)^# MIPS_DIFF_DATA\s+(\S+)\s+"
                r"(0x[0-9a-fA-F]+|\d+)\s+(0x[0-9a-fA-F]+|\d+)\s*$",
                assembly):
            data_words[(symbol, int(offset, 0))] = int(text_offset, 0)
        symbol_addresses = {
            symbol: int(address, 0)
            for symbol, address in re.findall(
                r"(?m)^# MIPS_DIFF_SYMBOL\s+(\S+)\s+"
                r"(0x[0-9a-fA-F]+|\d+)\s*$", assembly)
        }
        data_bytes: dict[tuple[str, int], int] = {}
        for symbol, payload in re.findall(
                r"(?m)^\s*# MIPS_DIFF_BYTES\s+(\S+)\s+"
                r"([0-9a-fA-F]+)\s*$",
                assembly):
            decoded = bytes.fromhex(payload)
            for offset, byte in enumerate(decoded):
                data_bytes[(symbol, offset)] = byte
        extents = {}
        for symbol, value in re.findall(r'(?m)^# MIPS_DIFF_EXTENT\s+([A-Za-z_]\w*)\s+(\d+)\s*$', assembly):
            size = int(value)
            if symbol not in symbol_addresses or not 0 < size <= 8*1024*1024:
                raise ValueError('extent requires linked identity and bounded positive size')
            if symbol in extents and extents[symbol] != size:
                raise ValueError('conflicting memory extent metadata')
            extents[symbol] = size
        return cls(name=name, instructions=instructions, labels=labels,
                   data_words=data_words,
                   symbol_addresses=symbol_addresses,
                   data_bytes=data_bytes, memory_extents=extents)

    @property
    def opcodes(self) -> tuple[str, ...]:
        return tuple(row.opcode for row in self.instructions)

    @property
    def symbols(self) -> frozenset[str]:
        names: set[str] = (
            {name for name, _offset in self.data_words} |
            {name for name, _offset in self.data_bytes} |
            set(self.symbol_addresses))
        for instruction in self.instructions:
            for operand in instruction.operands:
                for expression in re.findall(
                        r"%(?:hi|lo)\(([^)]+)\)", operand):
                    names.add(_parse_symbol_expression(expression)[0])
        return frozenset(names)

    @property
    def symbol_sizes(self) -> dict[str, int]:
        """Minimum regions implied by relocation addends in this function."""
        sizes: dict[str, int] = dict(self.memory_extents)
        for name, offset in self.data_words:
            sizes[name] = max(sizes.get(name, 0), offset + 4)
        for name, offset in self.data_bytes:
            sizes[name] = max(sizes.get(name, 0), offset + 1)
        for instruction in self.instructions:
            for operand in instruction.operands:
                for expression in re.findall(
                        r"%(?:hi|lo)\(([^)]+)\)", operand):
                    name, addend = _parse_symbol_expression(expression)
                    if addend >= 0:
                        # Four bytes safely covers the widest supported load
                        # or store at the final statically named offset.
                        sizes[name] = max(sizes.get(name, 0), addend + 4)
        return sizes

    def branch_target(self, token: str) -> int:
        target = token.strip().lstrip("$")
        if target in self.labels:
            return self.labels[target]
        # The normalized object dumps spell function-relative byte offsets as
        # bare hexadecimal ("48", "fc", "124").
        if re.fullmatch(r"(?:0x)?[0-9a-fA-F]+", target):
            offset = int(target[2:] if target.lower().startswith("0x") else target,
                         16)
            if offset % 4:
                raise UnsupportedInstruction(
                    f"unaligned branch target {token!r}")
            index = offset // 4
            if 0 <= index < len(self.instructions):
                return index
        raise UnsupportedInstruction(f"unresolved branch target {token!r}")


@dataclass(frozen=True)
class Region:
    name: str
    start: int
    size: int
    kind: str = "persistent"

    def contains(self, address: int, width: int = 1) -> bool:
        return self.start <= address and address + width <= self.start + self.size


class SymbolTable:
    """Stable synthetic addresses shared by both sides of a comparison."""

    def __init__(self, names: set[str] | frozenset[str],
                 known_addresses: dict[str, int] | None = None) -> None:
        self.addresses = {
            name: u32(address)
            for name, address in (known_addresses or {}).items()
            if name in names
        }
        self.linked_names = frozenset(self.addresses)
        synthetic_index = 0
        for name in sorted(names):
            if name in self.addresses:
                continue
            # The decomp linker names unresolved absolute constants D_HEX
            # (for example `D_3FFFF = 0x3FFFF` in undefined_syms_auto.txt).
            # Treating one as an ordinary data symbol silently changes an
            # arithmetic immediate into a synthetic 0x300... address.
            absolute = re.fullmatch(r"D_([0-9A-Fa-f]+)", name)
            if absolute is not None:
                self.addresses[name] = int(absolute.group(1), 16)
                continue
            self.addresses[name] = (
                0x30008000 + synthetic_index * SYMBOL_REGION_STRIDE)
            synthetic_index += 1

    def address(self, name: str) -> int:
        if name not in self.addresses:
            # Calls do not normally need an address, but function pointers do.
            digest = int(hashlib.sha256(name.encode()).hexdigest()[:6], 16)
            return 0x50000000 + (digest << 4)
        return self.addresses[name]


class Memory:
    """Sparse, bounded, big-endian memory with normalized region labels."""

    def __init__(self, regions: list[Region], data: dict[int, int], *,
                 initial_data: dict[int, int] | None = None,
                 dirty: set[int] | None = None,
                 default_seed: int = 0) -> None:
        self.regions = list(regions)
        self.data = dict(data)
        self.initial_data = (dict(data) if initial_data is None else
                             initial_data)
        self.dirty = set(dirty or ())
        self.default_seed = u32(default_seed)

    def clone(self) -> "Memory":
        return Memory(
            self.regions, self.data,
            initial_data=self.initial_data, dirty=self.dirty,
            default_seed=self.default_seed)

    def mark_clean(self) -> None:
        """Make the current seeded bytes the comparison baseline."""
        self.initial_data = dict(self.data)
        self.dirty.clear()

    def _default_byte(self, address: int) -> int:
        """Cheap deterministic filler materialized only when read."""
        value = u32(address) ^ u32(self.default_seed * 0x9E3779B1)
        value ^= value >> 16
        value = u32(value * 0x7FEB352D)
        value ^= value >> 15
        return value & 0xFF

    def _initial_byte(self, address: int) -> int:
        return self.initial_data.get(address, self._default_byte(address))

    def region(self, address: int, width: int = 1) -> Region:
        address = u32(address)
        for region in self.regions:
            if region.contains(address, width):
                return region
        raise MemoryFault(f"unmapped memory {address:#010x} width={width}")

    def read(self, address: int, width: int, *, signed: bool = False) -> int:
        address = u32(address)
        self.region(address, width)
        value = 0
        for offset in range(width):
            byte_address = address + offset
            value = (value << 8) | self.data.get(
                byte_address, self._default_byte(byte_address))
        return sign_extend(value, width * 8) if signed else value

    def write(self, address: int, width: int, value: int) -> None:
        address = u32(address)
        region = self.region(address, width)
        value &= (1 << (width * 8)) - 1
        for offset in range(width - 1, -1, -1):
            byte_address = address + offset
            self.data[byte_address] = value & 0xFF
            if region.kind != "stack":
                if self.data[byte_address] == self._initial_byte(byte_address):
                    self.dirty.discard(byte_address)
                else:
                    self.dirty.add(byte_address)
            value >>= 8

    def c_string(self, address: int, max_bytes: int = 256) -> bytes | None:
        """Return mapped NUL-terminated bytes, or None for a non-string pointer."""
        out = bytearray()
        try:
            for offset in range(max_bytes):
                byte_address = u32(address + offset)
                self.region(byte_address, 1)
                # Deterministic filler makes uninitialized pointers executable,
                # but it is not binary evidence that the pointee is a string.
                if byte_address not in self.data:
                    return None
                value = self.data[byte_address]
                if value == 0:
                    return bytes(out)
                out.append(value)
        except MemoryFault:
            return None
        return None

    def pointer(self, value: int) -> str:
        value = u32(value)
        for region in self.regions:
            if region.start <= value < region.start + region.size:
                suffix = value - region.start
                return region.name if suffix == 0 else f"{region.name}+0x{suffix:x}"
        return f"{value:#010x}"

    def persistent_state(self) -> dict[str, int]:
        return {
            self.pointer(address): self.data.get(address, 0)
            for address in sorted(self.dirty)
            if self.region(address).kind != "stack"
        }

    def apply_persistent_state(self, state: dict[str, int]) -> None:
        """Replace the current dirty overlay with a target checkpoint."""
        for address in tuple(self.dirty):
            if self.region(address).kind != "stack":
                self.data[address] = self._initial_byte(address)
                self.dirty.discard(address)
        by_name = {region.name: region for region in self.regions
                   if region.kind != "stack"}
        for pointer, value in state.items():
            match = re.fullmatch(r"(.+?)(?:\+0x([0-9a-f]+))?", pointer)
            if match is None or match.group(1) not in by_name:
                raise MemoryFault(f"checkpoint pointer {pointer!r} is unmapped")
            region = by_name[match.group(1)]
            self.write(
                region.start + int(match.group(2) or "0", 16), 1, value)

    def persistent_digest(self) -> str:
        digest = hashlib.sha256()
        for pointer, value in self.persistent_state().items():
            digest.update(pointer.encode())
            digest.update(bytes((value,)))
        return digest.hexdigest()


@dataclass(frozen=True)
class TestCase:
    name: str
    seed: int
    player_writes: tuple[tuple[int, int, int], ...] = ()
    global_writes: tuple[tuple[str, int, int], ...] = ()
    entry_registers: tuple[tuple[str, int], ...] = ()
    # Explicit opaque-call results are part of the simulated environment.
    # The ordinal indexes the opaque external-call trace, not instructions or
    # admitted concrete helper calls. Candidate-only helpers cannot shift it.
    call_returns: tuple[tuple[str, int, int], ...] = ()


@dataclass(frozen=True)
class CallEvent:
    ordinal: int
    callee: str
    arguments: tuple[str, ...]
    raw_arguments: tuple[int, ...]
    checkpoint_digest: str
    returned: int
    argument_provenance: tuple[str, ...] = ()
    trace_position: int = 0
    checkpoint_state: dict[str, int] = field(
        default_factory=dict, repr=False, compare=False)
    arity_known: bool = True
    abi_observations: tuple[dict, ...] = ()
    abi_contract: dict | None = None

    def to_dict(self) -> dict:
        value = asdict(self)
        value.pop("checkpoint_state", None)
        value["arguments"] = list(self.arguments)
        value["raw_arguments"] = [f"{item:#010x}" for item in self.raw_arguments]
        value["returned"] = f"{self.returned:#010x}"
        value["argument_provenance"] = list(self.argument_provenance)
        return value


@dataclass(frozen=True)
class WriteEvent:
    ordinal: int
    instruction: int
    address: str
    width: int
    value: int
    address_provenance: str = ""
    value_provenance: str = ""
    trace_position: int = 0
    raw_address: int = field(default=0, repr=False, compare=False)

    def to_dict(self) -> dict:
        value = asdict(self)
        value.pop("raw_address", None)
        value["value"] = f"{self.value:#0{self.width * 2 + 2}x}"
        return value


@dataclass(frozen=True)
class InstructionEvent:
    """One actually executed instruction with concrete dataflow evidence."""

    ordinal: int
    instruction: int
    text: str
    reads: tuple[tuple[str, int, str], ...] = ()
    writes: tuple[tuple[str, int, str], ...] = ()
    effect: str = ""

    def to_dict(self) -> dict:
        return {
            "ordinal": self.ordinal,
            "instruction": self.instruction,
            "text": self.text,
            "reads": [
                {"register": name, "value": f"{value:#010x}",
                 "provenance": provenance}
                for name, value, provenance in self.reads
            ],
            "writes": [
                {"register": name, "value": f"{value:#010x}",
                 "provenance": provenance}
                for name, value, provenance in self.writes
            ],
            "effect": self.effect,
        }


@dataclass
class RunResult:
    program: str
    status: str
    instruction_count: int
    branch_count: int
    calls: list[CallEvent]
    writes: list[WriteEvent]
    return_values: dict[str, int]
    abi_violations: list[str]
    error: str = ""
    persistent_state: dict[str, int] = field(default_factory=dict, repr=False)
    trace: list[InstructionEvent] = field(default_factory=list, repr=False)
    # Executed internal calls are diagnostic records, not opaque observables.
    # Their actual effects flow through shared memory and machine registers.
    concrete_calls: list[dict] = field(default_factory=list, repr=False)

    def to_dict(self) -> dict:
        return {
            "program": self.program,
            "status": self.status,
            "instruction_count": self.instruction_count,
            "branch_count": self.branch_count,
            "calls": [row.to_dict() for row in self.calls],
            "writes": [row.to_dict() for row in self.writes[:40]],
            "write_count": len(self.writes),
            "instruction_trace": [row.to_dict() for row in self.trace[:200]],
            "instruction_trace_count": len(self.trace),
            "return_values": {
                key: f"{value:#010x}" for key, value in self.return_values.items()
            },
            "abi_violations": list(self.abi_violations),
            "error": self.error,
            "concrete_calls": self.concrete_calls,
            "persistent_digest": _state_digest(self.persistent_state),
        }


@dataclass(frozen=True)
class DifferentialResult:
    case: str
    status: str
    reasons: tuple[str, ...]
    first_divergence: str
    target: RunResult
    candidate: RunResult

    def to_dict(self) -> dict:
        return {
            "case": self.case,
            "status": self.status,
            "reasons": list(self.reasons),
            "first_divergence": self.first_divergence,
            "target": self.target.to_dict(),
            "candidate": self.candidate.to_dict(),
        }


def _state_digest(state: dict[str, int]) -> str:
    if not state:
        return ""
    packed = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(packed).hexdigest()


def _stable_word(*parts: object) -> int:
    text = "\x1f".join(str(part) for part in parts).encode()
    return int(hashlib.sha256(text).hexdigest()[:8], 16)


def _execution_context(programs, case, environment=None):
    """One symbol/address closure for caller programs and admitted callees."""
    dependencies = tuple(leaf.program for _, leaf in sorted(environment.leaves.items())) if environment else ()
    for index, dependency in enumerate(dependencies):
        if not dependency.data_words:
            continue
        others = (*programs, *dependencies[:index], *dependencies[index+1:])
        for other in others:
            if (dependency.text_base < other.text_base+4*len(other.instructions)
                    and other.text_base < dependency.text_base+4*len(dependency.instructions)):
                raise ValueError('callee jump-table code-address range overlaps another program')
    names, addresses, sizes = set(_case_symbols(case)), {}, {}
    for program in (*programs, *dependencies):
        names.update(program.symbols)
        for name, address in program.symbol_addresses.items():
            if name in addresses and addresses[name] != address:
                raise ValueError(f'conflicting linker addresses for {name}: {addresses[name]:#x} != {address:#x}')
            addresses[name] = address
        for name, size in program.symbol_sizes.items():
            sizes[name] = max(sizes.get(name, 0), size)
    return SymbolTable(frozenset(names), addresses), sizes, dependencies


def _dependency_bytes(dependencies, symbols):
    initialized = {}
    for program in dependencies:
        values = dict(program.data_bytes)
        for (name, offset), text_offset in program.data_words.items():
            if text_offset % 4 or not 0 <= text_offset < 4*len(program.instructions):
                raise ValueError('callee jump-table offset lies outside its program')
            for i, byte in enumerate((program.text_base+text_offset).to_bytes(4, 'big')):
                values[(name, offset+i)] = byte
        for (name, offset), value in values.items():
            address = symbols.address(name)+offset
            if address in initialized and initialized[address] != value:
                raise ValueError('conflicting callee initialized bytes')
            initialized[address] = value
    return initialized


def _seed_memory(symbols: SymbolTable, case: TestCase,
                 minimum_sizes: dict[str, int] | None = None,
                 program: Program | None = None,
                 fallback_program: Program | None = None,
                 dependencies: tuple[Program, ...] = ()) -> Memory:
    requested_sizes: dict[str, int] = dict(minimum_sizes or {})
    for location, width, _value in case.global_writes:
        if location.startswith("@"):
            _scratch_address(location, width)
            continue
        name, offset = _split_global_location(location)
        size = offset + width
        # Linked globals do not occupy synthetic fixed-stride slots. Coverage
        # exploration can legitimately replay writes beyond 64 KiB in one;
        # apply the same linked-extent bounds/collision checks below as for
        # compiler-measured extents and static relocation addends.
        if size > SYMBOL_REGION_STRIDE and name not in symbols.linked_names:
            raise ValueError(
                f"global write {location!r} exceeds synthetic symbol stride")
        requested_sizes[name] = max(requested_sizes.get(name, 0), size)
    oversized = {name: size for name, size in requested_sizes.items()
                 if size > SYMBOL_REGION_STRIDE and name not in symbols.linked_names}
    if oversized:
        raise ValueError(
            f"symbol extent exceeds synthetic stride: {oversized}")
    for name, size in requested_sizes.items():
        if (not isinstance(size, int) or isinstance(size, bool) or size <= 0
                or size > 8*1024*1024 or symbols.address(name)+size > 0x100000000):
            raise ValueError('invalid or resource-limited symbol extent: '+name)
    regions = [
        Region("player", PLAYER_BASE, 0x2000),
        Region("arg1", ARG_POINTER_BASES["a1"], 0x2000),
        Region("arg2", ARG_POINTER_BASES["a2"], 0x2000),
        Region("arg3", ARG_POINTER_BASES["a3"], 0x2000),
        Region("stack", STACK_BASE, STACK_SIZE, "stack"),
    ]
    regions.extend(Region(f"arg{index}", address, 0x2000)
                   for index, address in STACK_ARG_POINTER_BASES.items())
    regions.extend(Region(
        f"&{name}", address, max(
            SYMBOL_REGION_SIZES.get(name, 0x100),
            requested_sizes.get(name, 0)))
                   for name, address in sorted(symbols.addresses.items()))
    # A large linked interval may contain linked aliases, which already share
    # address-keyed storage. It must never cover harness scratch/stack memory
    # or a synthetic symbol slot. The 8MiB ceiling above is a resource bound,
    # not a claim that every mapped address denotes real console RAM.
    for name, size in requested_sizes.items():
        if size <= SYMBOL_REGION_STRIDE:
            continue
        address = symbols.address(name)
        for region in regions:
            if region.name.startswith('&') and region.name[1:] in symbols.linked_names:
                continue
            if address < region.start+region.size and region.start < address+size:
                raise ValueError('linked symbol extent overlaps synthetic/reserved region: '+name)
    memory = Memory(regions, {}, default_seed=case.seed)
    if fallback_program is not None:
        for (name, offset), value in fallback_program.data_bytes.items():
            memory.write(symbols.address(name) + offset, 1, value)
    if program is not None:
        for (name, offset), value in program.data_bytes.items():
            memory.write(symbols.address(name) + offset, 1, value)
        for (name, offset), text_offset in program.data_words.items():
            memory.write(symbols.address(name) + offset, 4,
                         program.text_base + text_offset)
    for address, value in _dependency_bytes(dependencies, symbols).items():
        if address in memory.data and memory.data[address] != value:
            raise ValueError('caller/callee initialized data conflict')
        memory.write(address, 1, value)
    for offset, width, value in case.player_writes:
        memory.write(PLAYER_BASE + offset, width, value)
    for location, width, value in case.global_writes:
        if location.startswith("@"):
            if location.startswith('@entrysp') and any(
                    name.strip().lstrip('$') == 'sp' and u32(value) != INITIAL_SP
                    for name, value in case.entry_registers):
                raise ValueError('entry stack argument seeds require the standard initial sp')
            memory.write(_scratch_address(location, width), width, value)
            continue
        name, offset = _split_global_location(location)
        memory.write(symbols.address(name) + offset, width, value)
    memory.mark_clean()
    return memory


def _split_global_location(location: str) -> tuple[str, int]:
    """Decode the backward-compatible ``symbol+0xOFFSET`` case spelling."""
    match = re.fullmatch(
        r"([A-Za-z_][\w.$]*)(?:\+0x([0-9a-fA-F]+))?", location)
    if match is None:
        raise ValueError(f"invalid global write location {location!r}")
    return match.group(1), int(match.group(2) or "0", 16)


def _scratch_address(location: str, width: int) -> int:
    """Reserved, serialized input locations; never confused with game symbols."""
    stack = re.fullmatch(r"@entrysp\+0x([0-9a-fA-F]+)", location)
    if stack:
        offset = int(stack.group(1), 16)
        if width != 4 or offset % 4 or not 16 <= offset <= 60:
            raise ValueError('stack argument input must be an o32 word 4 through 15')
        return INITIAL_SP + offset
    match = re.fullmatch(r"@arg([1-9]|1[0-5])(?:\+0x([0-9a-fA-F]+))?", location)
    if match is None or width not in {1, 2, 4}:
        raise ValueError(f"invalid scratch input {location!r}/{width}")
    offset = int(match.group(2) or "0", 16)
    if offset + width > 0x2000:
        raise ValueError("scratch input exceeds mapped region")
    index = int(match.group(1))
    base = ARG_POINTER_BASES[f'a{index}'] if index < 4 else STACK_ARG_POINTER_BASES[index]
    return base + offset


def _case_symbols(case: TestCase) -> frozenset[str]:
    return frozenset(_split_global_location(location)[0]
                     for location, _width, _value in case.global_writes
                     if not location.startswith("@"))


def _seed_registers(case: TestCase) -> dict[str, int]:
    names = (
        "at", "v0", "v1", "a0", "a1", "a2", "a3",
        "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7", "t8", "t9",
        "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "fp", "gp",
        "sp", "ra",
    )
    registers = {name: _stable_word("entry", case.seed, name) for name in names}
    registers["a0"] = PLAYER_BASE
    registers["sp"] = INITIAL_SP
    registers["ra"] = RETURN_SENTINEL
    for name, value in case.entry_registers:
        clean = name.strip().lstrip("$")
        if clean not in REGISTER_NAMES or clean == "zero":
            raise ValueError(f"invalid entry register {name!r}")
        registers[clean] = u32(value)
    return registers


def mode16_cases() -> tuple[TestCase, ...]:
    """Boundary-shaped cases derived only from constants in target assembly."""
    cases = (
        TestCase(
            "initialize-positive-sine-and-snow",
            0x1601,
            player_writes=(
                (0x302, 2, 0), (0x308, 2, 0), (0x2FC, 4, 0x400),
                (0x7C, 4, 0x3B0), (0x14, 1, 0), (0x7E, 2, 0x123),
                (0x254, 4, 0x10203040), (0x264, 4, 0x20),
                (0x1C, 4, 0x1000), (0x20, 4, 0x2000),
                (0x24, 4, 0x3000), (0x40, 4, 0x40),
                (0x44, 4, 0x80), (0x48, 4, 0xC0),
                (0x0, 2, 7), (0x34, 2, 99),
            ),
            global_writes=(("gFrameCounter", 2, 1),),
        ),
        TestCase(
            "existing-negative-sine-no-snow",
            0x1602,
            player_writes=(
                (0x302, 2, 1), (0x308, 2, 1), (0x2FC, 4, 0),
                (0x7C, 4, 0x3CF), (0x14, 1, 1), (0x7E, 2, 0x8123),
                (0x254, 4, 0x50607080), (0x264, 4, 0xFFFFFFF0),
                (0x1C, 4, 0x11111111), (0x20, 4, 0x22222222),
                (0x24, 4, 0x33333333), (0x40, 4, 0x10),
                (0x44, 4, 0xFFFFFF00), (0x48, 4, 0x30),
            ),
            global_writes=(("gFrameCounter", 2, 2),),
        ),
        TestCase(
            "timer-clamp-boundary",
            0x1603,
            player_writes=(
                (0x302, 2, 2), (0x308, 2, 0), (0x2FC, 4, 0x400),
                (0x7C, 4, 0x3F0), (0x14, 1, 0), (0x7E, 2, 0),
                (0x254, 4, 0), (0x264, 4, 1),
                (0x1C, 4, 1), (0x20, 4, 2), (0x24, 4, 3),
                (0x40, 4, 4), (0x44, 4, 5), (0x48, 4, 6),
            ),
            global_writes=(("gFrameCounter", 2, 1),),
        ),
        TestCase(
            "sound-disabled-with-state-bit-clear",
            0x1604,
            player_writes=(
                (0x302, 2, 2), (0x308, 2, 1), (0x2FC, 4, 0x400),
                (0x7C, 4, 0), (0x14, 1, 1),
                (0x254, 4, 0x12345678), (0x264, 4, 2),
                (0x1C, 4, 10), (0x20, 4, 20), (0x24, 4, 30),
                (0x40, 4, 1), (0x44, 4, 4), (0x48, 4, 3),
                (0x0, 2, 0x1234),
            ),
            global_writes=(("gFrameCounter", 2, 1),),
        ),
        TestCase(
            "sound-enabled-with-state-bit-set",
            0x1605,
            player_writes=(
                (0x302, 2, 2), (0x308, 2, 1), (0x2FC, 4, 0x401),
                (0x7C, 4, 0), (0x14, 1, 0),
                (0x254, 4, 0x87654321), (0x264, 4, 1),
                (0x1C, 4, 40), (0x20, 4, 50), (0x24, 4, 60),
                (0x40, 4, 4), (0x44, 4, 5), (0x48, 4, 6),
                (0x0, 2, 0x5678),
            ),
            global_writes=(("gFrameCounter", 2, 1),),
        ),
    )
    # These two cases were retained by target-led coverage exploration because
    # they add the final previously unseen branch outcomes.  Keeping them in
    # the frozen panel makes all future repair rounds exercise every reachable
    # target instruction and both outcomes of all nine conditionals.
    return cases + (
        TestCase(
            "coverage-even-frame-counter", cases[0].seed,
            cases[0].player_writes,
            cases[0].global_writes + (("gFrameCounter", 2, 0),)),
        TestCase(
            "coverage-state-flags-clear", cases[0].seed,
            cases[0].player_writes + ((0x2FC, 4, 0),),
            cases[0].global_writes),
    )


class Runner:
    def __init__(self, program: Program, memory: Memory,
                 registers: dict[str, int], symbols: SymbolTable, *,
                 call_arities: dict[str, int] | None = None,
                 case_seed: int = 0, max_steps: int = 10_000,
                 return_registers: tuple[str, ...] = (),
                 call_returns: dict[tuple[str, int], int] | None = None,
                 capture_call_checkpoints: bool = False,
                 call_interventions: dict[int, CallEvent] | None = None,
                 write_interventions: dict[int, WriteEvent] | None = None,
                 callee_environment=None,
                 stack_ceiling: int | None = None
                 ) -> None:
        self.program = program
        self.memory = memory
        self.reg = {key: u32(value) for key, value in registers.items()}
        self.initial_reg = dict(self.reg)
        self.symbols = symbols
        self.origins = {
            key: (f"entry {key}={memory.pointer(value)}"
                  if key in {"a0", "sp"} else
                  f"entry {key}={u32(value):#010x}")
            for key, value in registers.items()
        }
        self.call_arities = call_arities or {}
        self.case_seed = case_seed
        self.max_steps = max_steps
        self.return_registers = return_registers
        self.call_returns = call_returns or {}
        self.capture_call_checkpoints = capture_call_checkpoints
        self.call_interventions = call_interventions or {}
        self.write_interventions = write_interventions or {}
        self.callee_environment = callee_environment
        self.callback_contracts = {}
        if callee_environment and getattr(callee_environment,'callbacks',()):
            from solver import callback_abi
            identity = callback_abi.program_identity(program)
            matching = [item for item in callee_environment.callbacks if item.identity == identity]
            if len(matching) > 1:
                raise ValueError('conflicting callback program contracts')
            if matching:
                self.callback_contracts = matching[0].contracts
        self.stack_ceiling = stack_ceiling
        self.concrete_calls = []
        self.call_count = 0  # All-call dynamic ordinal for concrete-call diagnostics.
        self.pc = 0
        self.steps = 0
        self.branches = 0
        self.calls: list[CallEvent] = []
        self.writes: list[WriteEvent] = []
        self.trace: list[InstructionEvent] = []
        self._pending_effect = ""

    def get(self, name: str) -> int:
        name = name.strip().lstrip("$")
        return 0 if name == "zero" else self.reg.get(name, 0)

    def origin(self, name: str) -> str:
        name = name.strip().lstrip("$")
        return "constant 0" if name == "zero" else \
            self.origins.get(name, f"unknown {name}")

    @staticmethod
    def _compact_origin(text: str, limit: int = 240) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        return text if len(text) <= limit else text[:limit - 3] + "..."

    def set(self, name: str, value: int, origin: str = "") -> None:
        name = name.strip().lstrip("$")
        if name != "zero":
            self.reg[name] = u32(value)
            self.origins[name] = self._compact_origin(
                origin or f"unknown result assigned to {name}")

    def immediate(self, text: str) -> int:
        token = text.strip()
        hi = re.fullmatch(r"%hi\(([^)]+)\)", token)
        if hi:
            name, addend = _parse_symbol_expression(hi.group(1))
            address = u32(self.symbols.address(name) + addend)
            return ((address + 0x8000) >> 16) & 0xFFFF
        lo = re.fullmatch(r"%lo\(([^)]+)\)", token)
        if lo:
            name, addend = _parse_symbol_expression(lo.group(1))
            return sign_extend(u32(self.symbols.address(name) + addend), 16)
        try:
            return int(token, 0)
        except ValueError as exc:
            raise UnsupportedInstruction(f"unsupported immediate {text!r}") from exc

    def address(self, operand: str) -> int:
        match = re.fullmatch(r"(.+)\((?:\$)?([\w]+)\)", operand.strip())
        if not match:
            raise UnsupportedInstruction(f"unsupported memory operand {operand!r}")
        return u32(self.get(match.group(2)) + self.immediate(match.group(1)))

    def _address_provenance(self, operand: str) -> str:
        match = re.fullmatch(r"(.+)\((?:\$)?([\w]+)\)", operand.strip())
        if not match:
            return operand.strip()
        offset = self.immediate(match.group(1))
        return self._compact_origin(
            f"{self.origin(match.group(2))} + {offset:#x}")

    @staticmethod
    def _operand_registers(instruction: cfg.Instruction) -> tuple[str, ...]:
        writes_first = instruction.opcode in {
            "move", "li", "lui", "addiu", "addi", "addu", "add",
            "subu", "sub", "or", "and", "xor", "nor", "not", "ori", "andi", "xori",
            "sll", "srl", "sra", "neg", "negu", "slti", "sltiu",
            "slt", "sltu", "mflo", "mfhi",
            "lb", "lbu", "lh", "lhu", "lw", "lwc1", "ldc1", "mfc1",
        }
        if instruction.opcode == "mtc1":
            operands = instruction.operands[:1]
        else:
            operands = instruction.operands[1:] if writes_first else \
                instruction.operands
        names: list[str] = []
        for operand in operands:
            for token in re.findall(r"\$?([A-Za-z][A-Za-z0-9]*)", operand):
                if token in REGISTER_NAMES and token not in names:
                    names.append(token)
        if instruction.opcode == 'sdc1' and instruction.operands:
            pair = re.fullmatch(r'\$?f(\d+)', instruction.operands[0])
            if pair and int(pair[1]) < 31:
                names.append(f'f{int(pair[1]) + 1}')
        return tuple(names)

    def _append_trace(self, instruction: cfg.Instruction,
                      before: dict[str, int],
                      before_origins: dict[str, str], effect: str = "") -> None:
        reads = tuple(
            (name, 0 if name == "zero" else before.get(name, 0),
             "constant 0" if name == "zero" else
             before_origins.get(name, f"unknown {name}"))
            for name in self._operand_registers(instruction)
        )
        changed = [
            name for name in sorted(set(before) | set(self.reg))
            if before.get(name, 0) != self.reg.get(name, 0) or
            before_origins.get(name, "") != self.origins.get(name, "")
        ]
        writes = tuple(
            (name, self.reg.get(name, 0), self.origin(name))
            for name in changed
        )
        self.trace.append(InstructionEvent(
            ordinal=len(self.trace), instruction=instruction.index,
            text=instruction.text, reads=reads, writes=writes,
            effect=effect))

    def _execute_traced_plain(self, instruction: cfg.Instruction) -> None:
        before, before_origins = dict(self.reg), dict(self.origins)
        self._pending_effect = ""
        try:
            self._execute_plain(instruction)
        except MemoryFault as exc:
            self._append_trace(
                instruction, before, before_origins, f"MEMORY FAULT: {exc}")
            raise
        self._append_trace(
            instruction, before, before_origins, self._pending_effect)

    def _record_write(self, instruction: cfg.Instruction, address: int,
                      width: int, value: int, address_provenance: str,
                      value_provenance: str) -> None:
        region = self.memory.region(address, width)
        if region.kind == "stack":
            return
        self.writes.append(WriteEvent(
            ordinal=len(self.writes), instruction=instruction.index,
            address=self.memory.pointer(address), width=width,
            value=value & ((1 << (width * 8)) - 1),
            address_provenance=address_provenance,
            value_provenance=value_provenance,
            trace_position=len(self.trace), raw_address=u32(address)))

    @staticmethod
    def _double_pair(register: str) -> tuple[str, str]:
        """Closed FR=0 paired-register representation, not FR=1 emulation."""
        match = re.fullmatch(r'\$?f(\d+)', register.strip())
        if not match or not 0 <= int(match[1]) <= 30 or int(match[1]) % 2:
            raise UnsupportedInstruction('invalid/odd double register: '+register)
        return f'f{int(match[1])}', f'f{int(match[1])+1}'

    def _load(self, instruction: cfg.Instruction, width: int,
              signed: bool) -> None:
        if len(instruction.operands) != 2:
            raise UnsupportedInstruction(instruction.text)
        target, memory_operand = instruction.operands
        pair = self._double_pair(target) if instruction.opcode == 'ldc1' else None
        address = self.address(memory_operand)
        if pair and address % 8:
            raise MemoryFault('unaligned doubleword load: '+instruction.text)
        address_provenance = self._address_provenance(memory_operand)
        try:
            self._check_callee_stack(address, width, reading=True)
            value = self.memory.read(address, width, signed=signed)
        except MemoryFault as exc:
            raise MemoryFault(
                f"{exc}; at i{instruction.index} `{instruction.text}`; "
                f"effective address from {address_provenance}") from None
        pointer = self.memory.pointer(address)
        displayed = f'{value:#018x}' if pair else f'{u32(value):#010x}'
        origin = self._compact_origin(
            f"i{instruction.index} {instruction.opcode} "
            f"{pointer}/{width} -> {displayed}")
        if pair:
            self.set(pair[0], value, origin+' low')
            self.set(pair[1], value >> 32, origin+' high')
        else:
            self.set(target, value, origin)
        self._pending_effect = (
            f"load {pointer}/{width}={displayed}; address from "
            f"{address_provenance}")

    def _store(self, instruction: cfg.Instruction, width: int) -> None:
        if len(instruction.operands) != 2:
            raise UnsupportedInstruction(instruction.text)
        source, memory_operand = instruction.operands
        pair = self._double_pair(source) if instruction.opcode == 'sdc1' else None
        address = self.address(memory_operand)
        if pair and address % 8:
            raise MemoryFault('unaligned doubleword store: '+instruction.text)
        value = self.get(source)
        address_provenance = self._address_provenance(memory_operand)
        value_provenance = self.origin(source)
        # Big-endian O32 partial-word stores. Only the selected bytes are
        # touched; reading/rewriting the entire aligned word would invent
        # memory accesses and persistent writes. See QEMU MIPS ldst_helper.c
        # helper_swl/helper_swr for independently implemented byte ordering.
        if instruction.opcode == 'swl':
            lane = address & 3
            width = 4 - lane
            value >>= 8 * lane
            value_provenance = self._compact_origin(
                f'{value_provenance}; swl high {width} bytes')
        elif instruction.opcode == 'swr':
            lane = address & 3
            width = lane + 1
            address &= ~3
            value &= (1 << (8 * width)) - 1
            address_provenance += f'; swr aligned start (effective lane {lane})'
            value_provenance = self._compact_origin(
                f'{value_provenance}; swr low {width} bytes')
        if pair:
            value = (self.get(pair[1]) << 32) | self.get(pair[0])
            value_provenance = self._compact_origin(
                self.origin(pair[1])+' high; '+self.origin(pair[0])+' low')
        try:
            self._check_callee_stack(address, width)
            previous = self.memory.read(address, width)
            self.memory.write(address, width, value)
        except MemoryFault as exc:
            raise MemoryFault(
                f"{exc}; at i{instruction.index} `{instruction.text}`; "
                f"effective address from {address_provenance}; value from "
                f"{value_provenance}") from None
        intervention = self.write_interventions.get(len(self.writes))
        if intervention is not None:
            if not intervention.raw_address:
                raise UnsupportedInstruction(
                    "write intervention is missing a raw target address")
            # Undo the candidate write before applying the target write. This
            # keeps the counterfactual continuation from retaining a spurious
            # side effect at the wrong address.
            self.memory.write(address, width, previous)
            self.memory.write(
                intervention.raw_address, intervention.width,
                intervention.value)
            self._record_write(
                instruction, intervention.raw_address, intervention.width,
                intervention.value, intervention.address_provenance,
                intervention.value_provenance)
            self._pending_effect = (
                "diagnostic resynchronization: candidate store "
                f"{self.memory.pointer(address)}/{width} replaced with target "
                f"{intervention.address}/{intervention.width}="
                f"{intervention.value:#x}")
        else:
            self._record_write(
                instruction, address, width, value,
                address_provenance, value_provenance)
            self._pending_effect = (
                f"store {self.memory.pointer(address)}/{width}="
                f"{value & ((1 << (width * 8)) - 1):#x}; value from "
                f"{value_provenance}")

    def _execute_plain(self, instruction: cfg.Instruction) -> None:
        op = instruction.opcode
        operands = instruction.operands
        if cfg.has_delay_slot(op):
            raise UnsupportedInstruction(
                f"control transfer in delay slot: {instruction.text}")
        if op == "nop":
            return
        if op == "move" and len(operands) == 2:
            self.set(operands[0], self.get(operands[1]), self.origin(operands[1]))
            return
        # COP1 transfer/load/store instructions preserve raw IEEE-754 bits.
        # Arithmetic is still deliberately unsupported, but these four are
        # sufficient for common zero/copy field initialization without any
        # host-float interpretation or rounding ambiguity.
        if op in {"mtc1", "mfc1"} and len(operands) == 2:
            self.set(operands[1] if op == "mtc1" else operands[0],
                     self.get(operands[0] if op == "mtc1" else operands[1]),
                     self.origin(operands[0] if op == "mtc1" else operands[1]))
            return
        if op in {"ctc1", "cfc1"} and len(operands) == 2:
            self.set(operands[1] if op == "ctc1" else operands[0],
                     self.get(operands[0] if op == "ctc1" else operands[1]),
                     self.origin(operands[0] if op == "ctc1" else operands[1]))
            return
        if op == "mov.d" and len(operands) == 2:
            target_index = int(operands[0].strip().lstrip("$f"))
            source_index = int(operands[1].strip().lstrip("$f"))
            if target_index & 1 or source_index & 1:
                raise UnsupportedInstruction(
                    f"odd double register at pc={instruction.index}: "
                    f"{instruction.text}")
            self.set(f"f{target_index}", self.get(f"f{source_index}"),
                     self.origin(f"f{source_index}"))
            self.set(f"f{target_index + 1}",
                     self.get(f"f{source_index + 1}"),
                     self.origin(f"f{source_index + 1}"))
            return
        if op == "cvt.s.w" and len(operands) == 2:
            value = _float32_bits(float(s32(self.get(operands[1]))))
            self.set(operands[0], value, self._compact_origin(
                f"i{instruction.index} cvt.s.w({self.origin(operands[1])}) "
                f"-> {value:#010x}"))
            return
        if op == "cvt.d.w" and len(operands) == 2:
            target_index = int(operands[0].strip().lstrip("$f"))
            if target_index & 1:
                raise UnsupportedInstruction(
                    f"odd double register at pc={instruction.index}: "
                    f"{instruction.text}")
            high, low = _float64_words(
                float(s32(self.get(operands[1]))))
            origin = self._compact_origin(
                f"i{instruction.index} cvt.d.w({self.origin(operands[1])})")
            self.set(f"f{target_index}", low, origin + " low")
            self.set(f"f{target_index + 1}", high, origin + " high")
            return
        if op in {"mul.s", "div.s"} and len(operands) == 3:
            left = _float32_from_bits(self.get(operands[1]))
            right = _float32_from_bits(self.get(operands[2]))
            if op == "div.s" and right == 0.0:
                result = math.copysign(math.inf, left * right)
            else:
                result = left * right if op == "mul.s" else left / right
            value = _float32_bits(result)
            self.set(operands[0], value, self._compact_origin(
                f"i{instruction.index} {op}({self.origin(operands[1])}, "
                f"{self.origin(operands[2])}) -> {value:#010x}"))
            return
        if op in {"add.d", "sub.d", "mul.d", "div.d"} and \
                len(operands) == 3:
            indexes = [int(operand.strip().lstrip("$f"))
                       for operand in operands]
            if any(index & 1 for index in indexes):
                raise UnsupportedInstruction(
                    f"odd double register at pc={instruction.index}: "
                    f"{instruction.text}")
            left = _float64_from_words(
                self.get(f"f{indexes[1] + 1}"), self.get(f"f{indexes[1]}"))
            right = _float64_from_words(
                self.get(f"f{indexes[2] + 1}"), self.get(f"f{indexes[2]}"))
            if op == "add.d":
                result = left + right
            elif op == "sub.d":
                result = left - right
            elif op == "mul.d":
                result = left * right
            elif right == 0.0:
                result = math.copysign(math.inf, left * right)
            else:
                result = left / right
            high, low = _float64_words(result)
            origin = self._compact_origin(
                f"i{instruction.index} {op}({self.origin(operands[1])}, "
                f"{self.origin(operands[2])})")
            self.set(f"f{indexes[0]}", low, origin + " low")
            self.set(f"f{indexes[0] + 1}", high, origin + " high")
            return
        if op == "cvt.w.s" and len(operands) == 2:
            value = _float32_from_bits(self.get(operands[1]))
            if not math.isfinite(value):
                raise UnsupportedInstruction(
                    "cvt.w.s nonfinite input requires unmodeled FCSR exception behavior")
            rounding = self.get("c1_fcsr") & 3
            if rounding == 1:
                converted = math.trunc(value)
            elif rounding == 2:
                converted = math.ceil(value)
            elif rounding == 3:
                converted = math.floor(value)
            else:
                converted = round(value)
            if not -0x80000000 <= converted <= 0x7FFFFFFF:
                raise UnsupportedInstruction(
                    "cvt.w.s out-of-range result requires unmodeled FCSR exception behavior")
            self.set(operands[0], u32(converted), self._compact_origin(
                f"i{instruction.index} cvt.w.s({self.origin(operands[1])}, "
                f"rounding={rounding}) -> {u32(converted):#010x}"))
            return
        if op == "li" and len(operands) == 2:
            value = self.immediate(operands[1])
            self.set(operands[0], value, f"constant {u32(value):#010x}")
            return
        if op == "lui" and len(operands) == 2:
            value = self.immediate(operands[1]) << 16
            self.set(operands[0], value,
                     f"i{instruction.index} lui {operands[1]} -> {u32(value):#010x}")
            return
        if op in {"addiu", "addi"} and len(operands) == 3:
            immediate = sign_extend(self.immediate(operands[2]), 16)
            value = self.get(operands[1]) + immediate
            self.set(operands[0], value, self._compact_origin(
                f"i{instruction.index} {op}({self.origin(operands[1])}, "
                f"{immediate:#x}) -> {u32(value):#010x}"))
            return
        if op == 'not' and len(operands) == 2:
            value = ~self.get(operands[1])
            self.set(operands[0], value, self._compact_origin(
                f'i{instruction.index} not({self.origin(operands[1])}) -> {u32(value):#010x}'))
            return
        if op in {"addu", "add", "subu", "sub", "or", "and", "xor", "nor"} \
                and len(operands) == 3:
            left, right = self.get(operands[1]), self.get(operands[2])
            if op in {"addu", "add"}:
                value = left + right
            elif op in {"subu", "sub"}:
                value = left - right
            elif op == "or":
                value = left | right
            elif op == "and":
                value = left & right
            elif op == 'nor':
                value = ~(left | right)
            else:
                value = left ^ right
            self.set(operands[0], value, self._compact_origin(
                f"i{instruction.index} {op}({self.origin(operands[1])}, "
                f"{self.origin(operands[2])}) -> {u32(value):#010x}"))
            return
        if op in {"ori", "andi", "xori"} and len(operands) == 3:
            left = self.get(operands[1])
            immediate = self.immediate(operands[2]) & 0xFFFF
            value = (left | immediate if op == "ori" else
                     left & immediate if op == "andi" else
                     left ^ immediate)
            self.set(operands[0], value, self._compact_origin(
                f"i{instruction.index} {op}({self.origin(operands[1])}, "
                f"{immediate:#x}) -> {u32(value):#010x}"))
            return
        if op in {"sll", "srl", "sra", "sllv", "srlv", "srav"} and \
                len(operands) == 3:
            value = self.get(operands[1])
            shift = (self.get(operands[2]) if op.endswith("v") else
                     self.immediate(operands[2])) & 31
            kind = op[:-1] if op.endswith("v") else op
            result = (u32(value << shift) if kind == "sll" else
                      value >> shift if kind == "srl" else
                      s32(value) >> shift)
            self.set(operands[0], result, self._compact_origin(
                f"i{instruction.index} {op}({self.origin(operands[1])}, "
                f"{shift}) -> {u32(result):#010x}"))
            return
        if op in {"neg", "negu"} and len(operands) == 2:
            if op == 'neg' and self.get(operands[1]) == 0x80000000:
                raise UnsupportedInstruction('signed neg overflow requires exception execution')
            value = -self.get(operands[1])
            self.set(operands[0], value, self._compact_origin(
                f"i{instruction.index} {op}({self.origin(operands[1])}) -> "
                f"{u32(value):#010x}"))
            return
        if op in {"slt", "sltu"} and len(operands) == 3:
            left, right = self.get(operands[1]), self.get(operands[2])
            result = (s32(left) < s32(right) if op == "slt" else left < right)
            self.set(operands[0], int(result), self._compact_origin(
                f"i{instruction.index} {op}({self.origin(operands[1])}, "
                f"{self.origin(operands[2])}) -> {int(result)}"))
            return
        if op in {"slti", "sltiu"} and len(operands) == 3:
            immediate = sign_extend(self.immediate(operands[2]), 16)
            if op == "slti":
                result = s32(self.get(operands[1])) < immediate
            else:
                result = self.get(operands[1]) < u32(immediate)
            self.set(operands[0], int(result), self._compact_origin(
                f"i{instruction.index} {op}({self.origin(operands[1])}, "
                f"{immediate:#x}) -> {int(result)}"))
            return
        if op in {"mult", "multu"} and len(operands) == 2:
            left, right = self.get(operands[0]), self.get(operands[1])
            product = (s32(left) * s32(right) if op == "mult" else left * right)
            product &= 0xFFFFFFFFFFFFFFFF
            origin = self._compact_origin(
                f"i{instruction.index} {op}({self.origin(operands[0])}, "
                f"{self.origin(operands[1])})")
            self.set("lo", product, origin + " low")
            self.set("hi", product >> 32, origin + " high")
            return
        if op in {"div", "divu"} and len(operands) in {2, 3}:
            numerator_name, denominator_name = operands[-2:]
            numerator, denominator = (
                self.get(numerator_name), self.get(denominator_name))
            if denominator == 0:
                raise UnsupportedInstruction(
                    f"division by zero at pc={instruction.index}")
            if op == "div":
                signed_numerator = s32(numerator)
                signed_denominator = s32(denominator)
                quotient = (abs(signed_numerator) // abs(signed_denominator))
                if (signed_numerator < 0) != (signed_denominator < 0):
                    quotient = -quotient
                remainder = signed_numerator - quotient * signed_denominator
            else:
                quotient, remainder = divmod(numerator, denominator)
            origin = self._compact_origin(
                f"i{instruction.index} {op}({self.origin(numerator_name)}, "
                f"{self.origin(denominator_name)})")
            self.set("lo", quotient, origin + " quotient")
            self.set("hi", remainder, origin + " remainder")
            return
        if op in {"mtlo", "mthi"} and len(operands) == 1:
            special = "lo" if op == "mtlo" else "hi"
            self.set(special, self.get(operands[0]), self._compact_origin(
                f'i{instruction.index} {op}({self.origin(operands[0])})'))
            return
        if op in {"mflo", "mfhi"} and len(operands) == 1:
            special = "lo" if op == "mflo" else "hi"
            self.set(operands[0], self.get(special), self.origin(special))
            return
        loads = {
            "lb": (1, True), "lbu": (1, False),
            "lh": (2, True), "lhu": (2, False), "lwc1": (4, False),
            "lw": (4, False),
            "ldc1": (8, False),
        }
        if op in loads:
            self._load(instruction, *loads[op])
            return
        stores = {"sb": 1, "sh": 2, "sw": 4, "swl": 4, "swr": 4, "swc1": 4, "sdc1": 8}
        if op in stores:
            self._store(instruction, stores[op])
            return
        raise UnsupportedInstruction(
            f"pc={instruction.index} unsupported: {instruction.text}")

    def _delay(self, index: int) -> None:
        if index >= len(self.program.instructions):
            # Workspace-normalized dumps trim terminal no-op padding.  Raw
            # target/candidate objects still contain the required `nop` after
            # a final `jr ra`, so execute that known no-op implicitly.
            self.trace.append(InstructionEvent(
                ordinal=len(self.trace), instruction=index, text="nop",
                effect="implicit terminal nop trimmed by normalizer"))
            self.steps += 1
            return
        self._execute_traced_plain(self.program.instructions[index])
        self.steps += 1

    def _branch_taken(self, instruction: cfg.Instruction) -> bool:
        op, operands = instruction.opcode, instruction.operands
        # Likely branches have the same predicate; execute() already annuls
        # their delay slot when not taken. Keep that control-flow distinction
        # on the original instruction rather than rewriting the program.
        if instruction.branch_likely:
            op = op[:-1]
        if op in {"beq", "bne"} and len(operands) == 3:
            equal = self.get(operands[0]) == self.get(operands[1])
            return equal if op == "beq" else not equal
        if op in {"beqz", "bnez", "bgez", "bgtz", "blez", "bltz"} \
                and len(operands) == 2:
            value = s32(self.get(operands[0]))
            return {
                "beqz": value == 0,
                "bnez": value != 0,
                "bgez": value >= 0,
                "bgtz": value > 0,
                "blez": value <= 0,
                "bltz": value < 0,
            }[op]
        raise UnsupportedInstruction(f"unsupported branch {instruction.text}")

    def _check_callee_stack(self, address, width, reading=False):
        if self.stack_ceiling is None or self.memory.region(address, width).kind != "stack":
            return
        if not self.get("sp") <= address or address + width > self.stack_ceiling:
            raise MemoryFault(f"callee stack access {address:#x}/{width} escapes active caller frame")
        if reading and any(address+i not in self.memory.data for i in range(width)):
            raise UnsupportedInstruction(f"uninitialized callee stack read {address:#x}/{width}")

    def _execute_callee(self, callee, instruction, ordinal, raw, arguments):
        spec = self.callee_environment.leaves[callee]
        before, before_origins = dict(self.reg), dict(self.origins)
        from solver.callee_execution import WordPairRunner
        runner_type = WordPairRunner if getattr(spec, 'word_pair_operation', None) else Runner
        child = runner_type(spec.program, self.memory, self.reg, self.symbols,
                       max_steps=max(1, self.max_steps-self.steps),
                       return_registers=spec.return_registers,
                       stack_ceiling=self.initial_reg["sp"])
        child.origins = dict(self.origins)
        run = child.execute()
        self.steps += run.instruction_count
        self.concrete_calls.append({"callee": callee, "ordinal": ordinal,
            "instruction": instruction.index, "arguments": list(arguments),
            "text_base": spec.program.text_base,
            "authority": spec.authority, "identity": spec.identity,
            # Preserve mutation inputs before serializing/truncating the child
            # trace. Child instruction IDs must never become caller coverage.
            "input_read_locations": sorted(_read_locations(run),key=str),
            "execution": run.to_dict()})
        if run.status == "memory_fault":
            raise MemoryFault(f"concrete callee {callee}: {run.error}")
        if run.status != "returned" or run.abi_violations:
            raise UnsupportedInstruction(f"concrete callee {callee}: {run.status}; "
                                         f"{run.error}; ABI={run.abi_violations}")
        self.reg, self.origins = child.reg, child.origins
        for register in self.reg:
            if self.reg[register] != before.get(register) or self.origins.get(register) != before_origins.get(register):
                self.origins[register] = self._compact_origin(
                    f"concrete {callee} call #{ordinal} result {register}={self.reg[register]:#010x}; "
                    f"callee origin: {self.origins.get(register,'unknown')}")
        # A call-result definition must be in the caller's dynamic value graph.
        # Otherwise a later store could be attributed to the pre-call v0 value.
        # This boundary event reuses the caller callsite index, never child IDs.
        self._trace_control(instruction, f"concrete callee {callee} returned; shared memory effects executed",
                            before, before_origins)
        # Retain externally visible writes at the caller's callsite. Nested
        # instruction indices must not masquerade as caller coverage/def-use.
        for event in run.writes:
            self.writes.append(replace(event, ordinal=len(self.writes),
                instruction=instruction.index, trace_position=len(self.trace),
                value_provenance=f"concrete {callee} i{event.instruction}: {event.value_provenance}"))

    def _call(self, instruction: cfg.Instruction, *,
              callee_override: str | None = None,
              arity_override: int | None = None,
              arity_known: bool = True,
              abi_contract: dict | None = None) -> None:
        if callee_override is None:
            if instruction.opcode != "jal" or len(instruction.operands) != 1:
                raise UnsupportedInstruction(f"unsupported call {instruction.text}")
            callee = instruction.operands[0].strip().lstrip("$")
            if re.fullmatch(r"(?:0x)?[0-9a-fA-F]+", callee):
                raise UnsupportedInstruction(f"unnamed call target {callee}")
        else:
            callee = callee_override
        arity = (arity_override if arity_override is not None else
                 self.call_arities.get(callee))
        if arity is None:
            raise UnsupportedInstruction(f"unknown call arity for {callee}")
        observations = []
        if not arity_known:
            # Diagnostic ABI window, NOT asserted arguments or comparison input.
            # Capture after the delay slot, before opaque clobbers. Stack words
            # can be locals/padding; unequal observations are only hypotheses.
            for index in range(8):
                location = f'a{index}' if index < 4 else f'sp+0x{index*4:x}'
                try:
                    value = self.get(location) if index < 4 else self.memory.read(u32(self.get('sp')+index*4),4)
                    origin = self.origin(location) if index < 4 else 'unclassified outgoing stack word'
                    observations.append({'word':index,'location':location,'value':value,
                        'label':self.memory.pointer(value),'origin':origin,'available':True})
                except MemoryFault:
                    observations.append({'word':index,'location':location,'available':False})
        raw_values: list[int] = []
        provenance_values: list[str] = []
        for index in range(arity):
            if index < 4:
                raw_values.append(self.get(f"a{index}"))
                provenance_values.append(self.origin(f"a{index}"))
                continue
            address = u32(self.get("sp") + 0x10 + (index - 4) * 4)
            raw_values.append(self.memory.read(address, 4))
            provenance_values.append(
                f"o32 stack argument a{index} from "
                f"{self.memory.pointer(address)}/4")
        raw = tuple(raw_values)
        argument_provenance = tuple(provenance_values)
        dynamic_ordinal = self.call_count
        self.call_count += 1
        environment = self.callee_environment
        if environment and callee in environment.leaves:
            if self.call_interventions or self.write_interventions:
                raise UnsupportedInstruction("concrete callees do not support diagnostic interventions")
            self._execute_callee(callee, instruction, dynamic_ordinal, raw,
                                 [self.memory.pointer(value) for value in raw])
            return
        # Observable-call identity is independent of compiler lowering. The
        # same external call after inline arithmetic or admitted helper code
        # must receive the same simulated return, clobbers and seed override.
        ordinal = len(self.calls)
        intervention = self.call_interventions.get(ordinal)
        event_callee = callee
        if intervention is not None:
            event_callee = intervention.callee
            raw = intervention.raw_arguments
            argument_provenance = tuple(
                f"diagnostic resynchronization from target call #{ordinal}"
                for _value in raw)
            for index, value in enumerate(raw):
                self.set(
                    f"a{index}", value,
                    f"diagnostic target argument at call #{ordinal}")
            self.memory.apply_persistent_state(intervention.checkpoint_state)
        argument_labels = [self.memory.pointer(value) for value in raw]
        if environment and event_callee in environment.leaves:
            raise UnsupportedInstruction("concrete callees do not support diagnostic interventions")
        output_effect = environment.outputs.get(event_callee) if environment else None
        if output_effect:
            argument_labels = output_effect.labels(self, raw, argument_labels)
        string_arguments = {
            "sprintf": (1,), "snprintf": (2,), "printf": (0,),
            "fprintf": (1,), "sscanf": (1,),
        }.get(event_callee, ())
        for index in string_arguments:
            if index >= len(raw):
                continue
            payload = self.memory.c_string(raw[index])
            if payload is not None:
                argument_labels[index] = f"cstr:{payload.hex()}"
        arguments = tuple(argument_labels)
        checkpoint = self.memory.persistent_digest()
        if self.capture_call_checkpoints:
            checkpoint_state = self.memory.persistent_state()
        else:
            # Ordinary differential runs need only enough state to explain
            # bytes touched before a call.  Full snapshots are reserved for
            # resynchronization, where they are actually replayed.
            checkpoint_state = {}
            for write in self.writes:
                for offset in range(write.width):
                    address = u32(write.raw_address + offset)
                    checkpoint_state[self.memory.pointer(address)] = \
                        self.memory.read(address, 1)
        returned = _stable_word(
            "return", self.case_seed, ordinal, event_callee, *arguments)
        if event_callee == "fixedSine":
            returned = u32((returned & 0x1FFF) - 0x1000)
        returned = u32(self.call_returns.get(
            (event_callee, ordinal), returned))
        if intervention is not None:
            returned = intervention.returned
        if output_effect:
            output_effect.apply(self, raw, arguments, ordinal, returned, instruction)
        for register in CALLER_SAVED:
            self.set(register, _stable_word(
                "clobber", self.case_seed, ordinal, event_callee, register,
                *arguments),
                f"deterministic clobber after {event_callee} call #{ordinal}")
        self.set("v0", returned, self._compact_origin(
            f"return from {event_callee} call #{ordinal}"
            f"({', '.join(arguments)}) "
            f"-> {returned:#010x}"))
        self.calls.append(CallEvent(
            ordinal=ordinal, callee=event_callee, arguments=arguments,
            raw_arguments=raw, checkpoint_digest=checkpoint,
            returned=returned, argument_provenance=argument_provenance,
            trace_position=len(self.trace), checkpoint_state=checkpoint_state,
            arity_known=arity_known, abi_observations=tuple(observations),abi_contract=abi_contract))

    def _trace_control(self, instruction: cfg.Instruction, effect: str,
                       before: dict[str, int] | None = None,
                       before_origins: dict[str, str] | None = None) -> None:
        self._append_trace(
            instruction, before if before is not None else dict(self.reg),
            before_origins if before_origins is not None else dict(self.origins),
            effect)

    def execute(self) -> RunResult:
        status, error = "running", ""
        try:
            while self.steps < self.max_steps:
                if not (0 <= self.pc < len(self.program.instructions)):
                    raise UnsupportedInstruction(f"pc escaped function: {self.pc}")
                instruction = self.program.instructions[self.pc]
                op = instruction.opcode
                self.steps += 1
                if op in {"b", "j"}:
                    if len(instruction.operands) != 1:
                        raise UnsupportedInstruction(instruction.text)
                    target = self.program.branch_target(instruction.operands[0])
                    self._trace_control(
                        instruction, f"unconditional control transfer to i{target}")
                    self._delay(self.pc + 1)
                    self.branches += 1
                    # A branch to itself with a no-op delay slot is a proven
                    # terminal behavior, not an arbitrary step-budget timeout.
                    # This is the common idle-thread leaf shape.  Do not fold
                    # loops whose delay slot has effects: repeated effects must
                    # remain visible and therefore retain the step-limit status.
                    if (target == self.pc and self.pc + 1 <
                            len(self.program.instructions) and
                            self.program.instructions[self.pc + 1].opcode ==
                            "nop"):
                        status = "nonreturn"
                        break
                    self.pc = target
                    continue
                if cfg.is_conditional_branch(op):
                    taken = self._branch_taken(instruction)
                    target = self.program.branch_target(instruction.operands[-1])
                    self._trace_control(
                        instruction,
                        f"branch {'taken' if taken else 'not taken'}; "
                        f"target i{target}")
                    if instruction.branch_likely and not taken:
                        self.pc += 2
                        self.branches += 1
                        continue
                    self._delay(self.pc + 1)
                    self.branches += 1
                    self.pc = target if taken else self.pc + 2
                    continue
                if op == "jal":
                    before, before_origins = dict(self.reg), dict(self.origins)
                    return_address = self.program.text_base + (self.pc + 2) * 4
                    self.set("ra", return_address,
                             f"return address after call at i{instruction.index}")
                    self._trace_control(
                        instruction, f"call {instruction.operands[0]}",
                        before, before_origins)
                    self._delay(self.pc + 1)
                    self._call(instruction)
                    self.pc += 2
                    continue
                if op == "jalr":
                    if len(instruction.operands) == 1:
                        link_register, target_register = "ra", instruction.operands[0]
                    elif len(instruction.operands) == 2:
                        link_register, target_register = instruction.operands
                    else:
                        raise UnsupportedInstruction(instruction.text)
                    before, before_origins = dict(self.reg), dict(self.origins)
                    target = self.get(target_register)
                    # Isolated functions do not contain the pointed-to callback.
                    # Keep its concrete identity in the event so target/candidate
                    # disagreement remains visible, but hook the body like every
                    # other external call. Unknown arity is NOT zero arity:
                    # the coverage hook omits arguments from its synthetic hash,
                    # but records an unclassified ABI window as explicit debt.
                    callee = f"indirect@{target:08x}"
                    return_address = self.program.text_base + (self.pc + 2) * 4
                    self.set(link_register, return_address,
                             f"return address after indirect call at i{instruction.index}")
                    self._trace_control(
                        instruction, f"indirect call {callee}",
                        before, before_origins)
                    self._delay(self.pc + 1)
                    contract = self.callback_contracts.get(instruction.index)
                    self._call(
                        instruction, callee_override=callee,
                        arity_override=contract['abi']['argument_words'] if contract else 0,
                        arity_known=contract is not None, abi_contract=contract)
                    self.pc += 2
                    continue
                if op == "jr":
                    if len(instruction.operands) != 1:
                        raise UnsupportedInstruction(instruction.text)
                    target = self.get(instruction.operands[0])
                    self._trace_control(
                        instruction, f"indirect control transfer to {target:#010x}")
                    self._delay(self.pc + 1)
                    if target == self.initial_reg.get("ra", RETURN_SENTINEL):
                        status = "returned"
                        break
                    if self.program.text_base <= target < \
                            self.program.text_base + len(self.program.instructions) * 4:
                        if (target-self.program.text_base) % 4:
                            raise UnsupportedInstruction(f'unaligned jr target {target:#x}')
                        self.pc = (target - self.program.text_base) // 4
                        continue
                    raise UnsupportedInstruction(f"unsupported jr target {target:#x}")
                self._execute_traced_plain(instruction)
                self.pc += 1
            else:
                status = "step_limit"
                error = f"exceeded {self.max_steps} instructions"
        except UnsupportedInstruction as exc:
            status, error = "unsupported", str(exc)
        except MemoryFault as exc:
            status, error = "memory_fault", str(exc)

        abi_violations = [
            name for name in CALLEE_SAVED
            if self.get(name) != self.initial_reg.get(name, 0)
        ]
        return RunResult(
            program=self.program.name, status=status,
            instruction_count=self.steps, branch_count=self.branches,
            calls=self.calls, writes=self.writes,
            return_values={name: self.get(name) for name in self.return_registers},
            abi_violations=abi_violations, error=error,
            persistent_state=self.memory.persistent_state(), trace=self.trace,
            concrete_calls=self.concrete_calls)


def _call_identity(event: CallEvent) -> tuple:
    return (event.callee, event.arguments, event.checkpoint_digest, event.returned)


def _write_divergence(target: RunResult, candidate: RunResult) -> str:
    for index in range(max(len(target.writes), len(candidate.writes))):
        left = target.writes[index] if index < len(target.writes) else None
        right = candidate.writes[index] if index < len(candidate.writes) else None
        if left is None:
            return f"extra candidate persistent write #{index}: {right.to_dict()}"
        if right is None:
            return f"missing candidate persistent write #{index}: {left.to_dict()}"
        identity_left = (left.address, left.width, left.value)
        identity_right = (right.address, right.width, right.value)
        if identity_left != identity_right:
            return (
                f"write #{index} differs: target {left.address}/{left.width}="
                f"{left.value:#x}; candidate {right.address}/{right.width}="
                f"{right.value:#x}")
    return ""


def _first_divergence(target: RunResult, candidate: RunResult) -> str:
    if target.status != candidate.status:
        return (f"execution status differs: target {target.status}"
                f"{': ' + target.error if target.error else ''}; candidate "
                f"{candidate.status}"
                f"{': ' + candidate.error if candidate.error else ''}")
    for index in range(max(len(target.calls), len(candidate.calls))):
        left = target.calls[index] if index < len(target.calls) else None
        right = candidate.calls[index] if index < len(candidate.calls) else None
        if left is None:
            return f"extra candidate call #{index}: {right.callee}{right.arguments}"
        if right is None:
            return f"missing candidate call #{index}: {left.callee}{left.arguments}"
        if _call_identity(left) != _call_identity(right):
            if (left.callee, left.arguments) == (right.callee, right.arguments):
                return (f"call #{index} {left.callee}{left.arguments} observes "
                        "different persistent memory at entry")
            return (
                f"call #{index} differs: target {left.callee}{left.arguments}; "
                f"candidate {right.callee}{right.arguments}")
    write_divergence = _write_divergence(target, candidate)
    if write_divergence:
        return write_divergence
    all_addresses = sorted(set(target.persistent_state) |
                           set(candidate.persistent_state))
    for address in all_addresses:
        left = target.persistent_state.get(address)
        right = candidate.persistent_state.get(address)
        if left != right:
            return (f"final memory differs at {address}: target {left:#04x}; "
                    f"candidate {right:#04x}")
    if target.return_values != candidate.return_values:
        return (f"return values differ: target {target.return_values}; "
                f"candidate {candidate.return_values}")
    return ""


def compare_programs(target: Program, candidate: Program, case: TestCase, *,
                     call_arities: dict[str, int] | None = None,
                     return_registers: tuple[str, ...] = (),
                     max_steps: int = 10_000, callee_environment=None) -> DifferentialResult:
    symbols, symbol_sizes, dependencies = _execution_context((target,candidate),case,callee_environment)
    target_memory = _seed_memory(
        symbols, case, symbol_sizes, program=target,
        fallback_program=candidate, dependencies=dependencies)
    candidate_memory = _seed_memory(
        symbols, case, symbol_sizes, program=candidate,
        fallback_program=target, dependencies=dependencies)
    registers = _seed_registers(case)
    left = Runner(
        target, target_memory, registers, symbols,
        call_arities=call_arities, case_seed=case.seed,
        max_steps=max_steps, return_registers=return_registers, callee_environment=callee_environment,
        call_returns={(callee, ordinal): value
                      for callee, ordinal, value in case.call_returns}).execute()
    right = Runner(
        candidate, candidate_memory, registers, symbols,
        call_arities=call_arities, case_seed=case.seed,
        max_steps=max_steps, return_registers=return_registers, callee_environment=callee_environment,
        call_returns={(callee, ordinal): value
                      for callee, ordinal, value in case.call_returns}).execute()

    return _compare_runs(case, left, right)


def _compare_runs(case: TestCase, left: RunResult,
                  right: RunResult) -> DifferentialResult:
    """Apply the ordinary semantic gates to two completed executions."""

    reasons: list[str] = []
    if left.status not in COMPLETED_STATUSES:
        status = "inconclusive"
        reasons.append(f"target execution {left.status}: {left.error}")
    elif left.status == "returned" and left.abi_violations:
        status = "inconclusive"
        reasons.append("target control violates callee-saved ABI")
    elif right.status == "unsupported":
        status = "inconclusive"
        reasons.append(f"candidate execution unsupported: {right.error}")
    elif right.status not in COMPLETED_STATUSES:
        status = "failed"
        reasons.append(f"candidate execution {right.status}: {right.error}")
    elif left.status != right.status:
        status = "failed"
        reasons.append(
            f"terminal behavior differs: target {left.status}; "
            f"candidate {right.status}")
    else:
        if [_call_identity(row) for row in left.calls] != \
                [_call_identity(row) for row in right.calls]:
            reasons.append("external call trace or call-time memory differs")
        if left.persistent_state != right.persistent_state:
            reasons.append("final persistent memory differs")
        if (left.status == "returned" and
                left.return_values != right.return_values):
            reasons.append("declared return registers differ")
        if right.status == "returned" and right.abi_violations:
            reasons.append("candidate violates callee-saved ABI")
        status = "passed" if not reasons else "failed"
    return DifferentialResult(
        case=case.name, status=status, reasons=tuple(reasons),
        first_divergence=_first_divergence(left, right),
        target=left, candidate=right)


def repair_feedback(result: DifferentialResult) -> str:
    """Render one bounded, model-ready behavioral observation."""
    lines = [
        f"DIFFERENTIAL EXECUTION: {result.status.upper()}",
        f"case: {result.case}",
    ]
    if result.first_divergence:
        lines.append(f"first divergence: {result.first_divergence}")
    lines.append(call_traceback(result))
    write_divergence = _write_divergence(result.target, result.candidate)
    if write_divergence and write_divergence != result.first_divergence:
        lines.append(f"earliest persistent-write divergence: {write_divergence}")
    if result.reasons:
        lines.append("failed gates: " + "; ".join(result.reasons))
    lines.append(
        "dynamic instructions: "
        f"target={result.target.instruction_count}, "
        f"candidate={result.candidate.instruction_count}")
    return "\n".join(lines)


def _render_call(event: CallEvent | None) -> str:
    if event is None:
        return "<none>"
    return f"{event.callee}({', '.join(event.arguments)})"


def call_traceback(result: DifferentialResult, max_calls: int = 32) -> str:
    """Render a compact side-by-side external-call trace for model feedback.

    ``=`` means callee, arguments, and persistent memory at call entry agree;
    ``~`` means the same call and arguments observed different memory; and
    ``!`` means the callee or normalized arguments differ.
    """
    lines = [
        "call traceback (= same call/state, ~ same call but memory differs, "
        "! callee/arguments differ):"
    ]
    total = max(len(result.target.calls), len(result.candidate.calls))
    if total == 0:
        lines.append("  (no external calls)")
        return "\n".join(lines)
    for index in range(min(total, max_calls)):
        left = result.target.calls[index] if index < len(result.target.calls) else None
        right = (result.candidate.calls[index]
                 if index < len(result.candidate.calls) else None)
        if left is not None and right is not None and \
                (left.callee, left.arguments) == (right.callee, right.arguments):
            if left.checkpoint_digest == right.checkpoint_digest:
                lines.append(f"  [{index:02d}] = {_render_call(left)}")
            else:
                lines.append(
                    f"  [{index:02d}] ~ {_render_call(left)} "
                    "[persistent memory differs at entry]")
        else:
            lines.append(
                f"  [{index:02d}] ! target: {_render_call(left)} | "
                f"candidate: {_render_call(right)}")
    if total > max_calls:
        lines.append(f"  ... {total - max_calls} further call(s) omitted")
    return "\n".join(lines)


def _matching_call_checkpoint_prefix(result: DifferentialResult) -> int:
    count = 0
    for left, right in zip(result.target.calls, result.candidate.calls):
        if _call_identity(left) != _call_identity(right):
            break
        count += 1
    return count


def _matching_write_prefix(result: DifferentialResult) -> int:
    count = 0
    for left, right in zip(result.target.writes, result.candidate.writes):
        if (left.address, left.width, left.value) != \
                (right.address, right.width, right.value):
            break
        count += 1
    return count


def semantic_prefix_contract(result: DifferentialResult) -> str:
    """Describe what a future edit must preserve for this concrete case."""
    calls = _matching_call_checkpoint_prefix(result)
    writes = _matching_write_prefix(result)
    return (
        "verified semantic-prefix contract for this case: "
        f"{calls} external call checkpoint(s) and {writes} persistent "
        "write(s) match; rerun the complete function from entry after every "
        "edit")


def _render_instruction_event(event: InstructionEvent) -> str:
    fields = [
        f"dyn#{event.ordinal:03d}/i{event.instruction:03d} {event.text}"
    ]
    if event.reads:
        fields.append("in " + ", ".join(
            f"{name}={value:#010x} <= {provenance}"
            for name, value, provenance in event.reads))
    if event.writes:
        fields.append("out " + ", ".join(
            f"{name}={value:#010x} <= {provenance}"
            for name, value, provenance in event.writes))
    if event.effect:
        fields.append(event.effect)
    return " | ".join(fields)


def _trace_window(run: RunResult, end: int, max_steps: int) -> list[str]:
    end = min(max(0, end), len(run.trace))
    start = max(0, end - max_steps)
    return [_render_instruction_event(event) for event in run.trace[start:end]]


def _first_call_difference(
        result: DifferentialResult) -> tuple[int, CallEvent | None,
                                              CallEvent | None] | None:
    for index in range(max(len(result.target.calls),
                           len(result.candidate.calls))):
        left = result.target.calls[index] if index < len(result.target.calls) \
            else None
        right = result.candidate.calls[index] \
            if index < len(result.candidate.calls) else None
        if left is None or right is None or _call_identity(left) != \
                _call_identity(right):
            return index, left, right
    return None


def _first_write_difference(
        result: DifferentialResult) -> tuple[int, WriteEvent | None,
                                              WriteEvent | None] | None:
    for index in range(max(len(result.target.writes),
                           len(result.candidate.writes))):
        left = result.target.writes[index] \
            if index < len(result.target.writes) else None
        right = result.candidate.writes[index] \
            if index < len(result.candidate.writes) else None
        if left is None or right is None or \
                (left.address, left.width, left.value) != \
                (right.address, right.width, right.value):
            return index, left, right
    return None


def _first_aligned_write_difference(
        result: DifferentialResult) -> tuple[str, WriteEvent | None,
                                              WriteEvent | None] | None:
    """Find the first write edit, distinguishing insertions from replacements."""
    left_ids = [
        (row.address, row.width, row.value) for row in result.target.writes
    ]
    right_ids = [
        (row.address, row.width, row.value) for row in result.candidate.writes
    ]
    matcher = difflib.SequenceMatcher(
        None, left_ids, right_ids, autojunk=False)
    for tag, left_start, left_end, right_start, right_end in \
            matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            right = result.candidate.writes[right_start]
            return (
                f"extra candidate write #{right_start} before target write "
                f"#{left_start}", None, right)
        if tag == "delete":
            left = result.target.writes[left_start]
            return (
                f"missing candidate write corresponding to target write "
                f"#{left_start}", left, None)
        left = result.target.writes[left_start] if left_start < left_end else None
        right = (result.candidate.writes[right_start]
                 if right_start < right_end else None)
        return (
            f"replacement block: target writes [{left_start}:{left_end}], "
            f"candidate writes [{right_start}:{right_end}]", left, right)
    return None


def _write_provenance(label: str, event: WriteEvent | None) -> list[str]:
    if event is None:
        return [f"  {label}: <none>"]
    return [
        f"  {label}: {event.address}/{event.width}={event.value:#x} at "
        f"i{event.instruction}",
        f"    address provenance: {event.address_provenance}",
        f"    value provenance: {event.value_provenance}",
    ]


def _pointer_parts(pointer: str) -> tuple[str, int] | None:
    match = re.fullmatch(r"(.+?)(?:\+0x([0-9a-f]+))?", pointer)
    if not match:
        return None
    return match.group(1), int(match.group(2) or "0", 16)


def _last_writer(run: RunResult, byte: str,
                 before_trace: int) -> WriteEvent | None:
    wanted = _pointer_parts(byte)
    if wanted is None:
        return None
    wanted_region, wanted_offset = wanted
    for event in reversed(run.writes):
        if event.trace_position > before_trace:
            continue
        location = _pointer_parts(event.address)
        if location is None or location[0] != wanted_region:
            continue
        if location[1] <= wanted_offset < location[1] + event.width:
            return event
    return None


def _call_memory_delta(
        target_run: RunResult, target_call: CallEvent,
        candidate_run: RunResult, candidate_call: CallEvent,
        limit: int = 8) -> list[str]:
    names = sorted(set(target_call.checkpoint_state) |
                   set(candidate_call.checkpoint_state))
    changed = [name for name in names
               if target_call.checkpoint_state.get(name) !=
               candidate_call.checkpoint_state.get(name)]
    lines = [f"  call-entry memory delta: {len(changed)} byte(s) differ"]
    for name in changed[:limit]:
        left = target_call.checkpoint_state.get(name)
        right = candidate_call.checkpoint_state.get(name)
        left_writer = _last_writer(
            target_run, name, target_call.trace_position)
        right_writer = _last_writer(
            candidate_run, name, candidate_call.trace_position)
        left_text = "<initial>" if left is None else f"{left:#04x}"
        right_text = "<initial>" if right is None else f"{right:#04x}"
        lines.append(
            f"    {name}: target {left_text} <= "
            f"{_writer_summary(left_writer)}; candidate {right_text} <= "
            f"{_writer_summary(right_writer)}")
    if len(changed) > limit:
        lines.append(f"    ... {len(changed) - limit} further byte(s) omitted")
    return lines


def _writer_summary(event: WriteEvent | None) -> str:
    if event is None:
        return "initial seeded memory"
    return (
        f"write#{event.ordinal} i{event.instruction} "
        f"{event.address}/{event.width}={event.value:#x} "
        f"({event.value_provenance})")


def causal_slice(result: DifferentialResult, max_steps: int = 12) -> str:
    """Render executed, concrete data/control evidence for the next mismatch."""
    lines = ["causal backward-slice evidence:"]
    status_differs = result.target.status != result.candidate.status
    if status_differs:
        # The successful side may have avoided the fault via a different
        # earlier guard. Showing only the faulting trace conceals that useful
        # counterexample. Keep both concrete windows bounded; do not claim
        # instruction alignment or waive either execution outcome.
        for label,execution in (("target",result.target),("candidate",result.candidate)):
            lines.append(label + (" returned comparison window:" if execution.status == "returned"
                                  else " terminal/fault window:"))
            lines.extend("  " + row for row in _trace_window(
                execution, len(execution.trace), max_steps))
        lines.append(
            "later missing calls are consequences of this terminal status, "
            "not independent call divergences")
    call_difference = None if status_differs else _first_call_difference(result)
    if call_difference is not None:
        index, left, right = call_difference
        lines.append(f"call #{index}:")
        lines.append(f"  target: {_render_call(left)}")
        lines.append(f"  candidate: {_render_call(right)}")
        if left is None or right is None:
            # Call-count divergence can be caused by a guard well before the
            # short call window. Show executed decisions on BOTH sides since
            # their last matching checkpoint, without claiming CFG alignment.
            prefix = _matching_call_checkpoint_prefix(result)
            lines.append('  control histories are independent executions, not aligned branches:')
            for label, run, call in (("target", result.target, left),
                                     ("candidate", result.candidate, right)):
                start = run.calls[prefix - 1].trace_position if prefix else 0
                end = call.trace_position if call else len(run.trace)
                decisions = [event for event in run.trace[start:end]
                             if event.effect.startswith(('branch taken;', 'branch not taken;'))]
                limit = max(1, max_steps)
                lines.append(f'  {label} branch decisions after {prefix} matching call checkpoints '
                             f'(omitted {max(0,len(decisions)-limit)} earlier decisions):')
                lines.extend('    ' + _render_instruction_event(event)
                             for event in decisions[-limit:])
                if call is None:
                    lines.append(f'  {label} terminal comparison window ({run.status}):')
                    lines.extend('    ' + row for row in _trace_window(run,len(run.trace),max_steps))
        arity = max(
            len(left.raw_arguments) if left else 0,
            len(right.raw_arguments) if right else 0)
        for arg_index in range(arity):
            left_value = left.raw_arguments[arg_index] \
                if left and arg_index < len(left.raw_arguments) else None
            right_value = right.raw_arguments[arg_index] \
                if right and arg_index < len(right.raw_arguments) else None
            left_origin = left.argument_provenance[arg_index] \
                if left and arg_index < len(left.argument_provenance) else "<none>"
            right_origin = right.argument_provenance[arg_index] \
                if right and arg_index < len(right.argument_provenance) else "<none>"
            normalized_equal = bool(left and right and left.callee == right.callee
                and arg_index < len(left.arguments) and arg_index < len(right.arguments)
                and left.arguments[arg_index] == right.arguments[arg_index])
            marker = "=" if normalized_equal else "!"
            left_text = "<none>" if left_value is None else f"{left_value:#010x}"
            right_text = "<none>" if right_value is None else f"{right_value:#010x}"
            lines.append(
                f"  {marker} a{arg_index}: target {left_text} <= {left_origin}")
            lines.append(
                f"         candidate {right_text} <= {right_origin}")
            if normalized_equal and left_value != right_value:
                lines.append('         normalized observable agrees; physical address difference is not a repair objective')
        if left and right and (left.callee, left.arguments) == \
                (right.callee, right.arguments) and \
                left.checkpoint_digest != right.checkpoint_digest:
            lines.append(
                "  call arguments agree, but persistent memory differs at entry")
            lines.extend(_call_memory_delta(
                result.target, left, result.candidate, right))
        if left:
            lines.append("  executed target window:")
            lines.extend("    " + row for row in _trace_window(
                result.target, left.trace_position, max_steps))
        if right:
            lines.append("  executed candidate window:")
            lines.extend("    " + row for row in _trace_window(
                result.candidate, right.trace_position, max_steps))

    write_difference = _first_aligned_write_difference(result)
    if write_difference is not None:
        description, left, right = write_difference
        lines.append(f"persistent-write alignment: {description}")
        lines.extend(_write_provenance("target", left))
        lines.extend(_write_provenance("candidate", right))
        if left:
            lines.append("  executed target window ending at the store:")
            lines.extend("    " + row for row in _trace_window(
                result.target, left.trace_position + 1, max_steps))
        if right:
            lines.append("  executed candidate window ending at the store:")
            lines.extend("    " + row for row in _trace_window(
                result.candidate, right.trace_position + 1, max_steps))

    if call_difference is None and write_difference is None:
        lines.append("  no call or persistent-write mismatch was found")
    return "\n".join(lines)


def causal_feedback(result: DifferentialResult, max_steps: int = 12) -> str:
    """Model observation that summarizes the prefix and expands only the cause."""
    lines = [
        f"DIFFERENTIAL EXECUTION: {result.status.upper()}",
        f"case: {result.case}",
        ("memory notation: address/width=value uses a raw byte address and an "
         "access width in bytes; `/2` is not division or C pointer scaling"),
        semantic_prefix_contract(result),
    ]
    if result.first_divergence:
        lines.append(f"first divergence: {result.first_divergence}")
    lines.append(causal_slice(result, max_steps=max_steps))
    if result.reasons:
        lines.append("failed gates: " + "; ".join(result.reasons))
    return "\n".join(lines)


@dataclass(frozen=True)
class ResynchronizedObservation:
    """One mismatch exposed before a diagnostic state intervention."""
    case: str
    stage: int
    kind: str
    signature: str
    first_divergence: str
    evidence: str
    intervention: str
    intervention_supported: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ResynchronizationReport:
    """Independent mismatch sequence for one concrete input.

    These reports are diagnostic counterfactuals. Only the ordinary,
    unintervened ``DifferentialResult`` can pass or fail a semantic gate.
    """
    case: str
    observations: tuple[ResynchronizedObservation, ...]
    terminal_status: str
    terminal_first_divergence: str
    stop_reason: str

    def to_dict(self) -> dict:
        return {
            "case": self.case,
            "observations": [row.to_dict() for row in self.observations],
            "terminal_status": self.terminal_status,
            "terminal_first_divergence": self.terminal_first_divergence,
            "stop_reason": self.stop_reason,
        }


def _resynchronization_observation(
        result: DifferentialResult, stage: int
        ) -> tuple[ResynchronizedObservation, tuple[str, int, object] | None]:
    if result.target.status != result.candidate.status:
        signature = re.sub(
            r"0x[0-9a-fA-F]+", "<value>",
            f"status:{result.target.status}->{result.candidate.status}:"
            f"{result.candidate.error}")
        observation = ResynchronizedObservation(
            result.case, stage, "terminal", signature,
            result.first_divergence, causal_slice(result, max_steps=12),
            "unsupported: repair the recorded terminal/fault instruction "
            "before resynchronizing later observables",
            False)
        return observation, None
    call_difference = _first_call_difference(result)
    if call_difference is not None:
        index, left, right = call_difference
        if left is None:
            signature = f"call#{index}:extra-candidate:{right.callee}"
        elif right is None:
            signature = f"call#{index}:missing-candidate:{left.callee}"
        else:
            different_arguments = tuple(
                arg for arg in range(max(
                    len(left.raw_arguments), len(right.raw_arguments)))
                if (left.raw_arguments[arg]
                    if arg < len(left.raw_arguments) else None) !=
                   (right.raw_arguments[arg]
                    if arg < len(right.raw_arguments) else None))
            memory_differs = (
                left.checkpoint_digest != right.checkpoint_digest)
            signature = (
                f"call#{index}:{left.callee}/{len(left.raw_arguments)}->"
                f"{right.callee}/{len(right.raw_arguments)}:"
                f"args={different_arguments}:memory={memory_differs}")
        supported = left is not None and right is not None
        intervention = (
            f"replace candidate call checkpoint #{index} with target callee, "
            "arguments, return, clobbers, and persistent memory"
            if supported else
            "unsupported: inserting or removing a call requires control-flow "
            "resynchronization")
        observation = ResynchronizedObservation(
            result.case, stage, "call", signature,
            result.first_divergence, causal_slice(result, max_steps=6),
            intervention, supported)
        action = ("call", index, left) if supported else None
        return observation, action

    write_difference = _first_aligned_write_difference(result)
    if write_difference is not None:
        description, left, right = write_difference
        left_shape = (f"{left.address}/{left.width}" if left else "<none>")
        right_shape = (f"{right.address}/{right.width}" if right else "<none>")
        signature = f"write:{description.split(':', 1)[0]}:{left_shape}->{right_shape}"
        supported = left is not None and right is not None
        intervention = (
            f"undo candidate write #{right.ordinal} and apply target "
            f"{left.address}/{left.width}={left.value:#x}"
            if supported else
            "unsupported: inserting or suppressing a write requires a later "
            "observable checkpoint")
        observation = ResynchronizedObservation(
            result.case, stage, "write", signature,
            result.first_divergence, causal_slice(result, max_steps=6),
            intervention, supported)
        action = ("write", right.ordinal, left) if supported else None
        return observation, action

    signature = re.sub(r"0x[0-9a-fA-F]+", "<value>",
                       result.first_divergence or "; ".join(result.reasons))
    observation = ResynchronizedObservation(
        result.case, stage, "terminal", signature,
        result.first_divergence, causal_slice(result, max_steps=6),
        "unsupported: no call or persistent-write checkpoint to replace",
        False)
    return observation, None


def resynchronized_report(
        target: Program, candidate: Program, case: TestCase, *,
        call_arities: dict[str, int] | None = None,
        return_registers: tuple[str, ...] = (),
        max_divergences: int = 3, max_steps: int = 10_000
        ) -> ResynchronizationReport:
    """Expose later mismatches by replacing prior semantic checkpoints.

    Each candidate rerun starts from the original input. Accumulated
    interventions replace a mismatching call or write with the corresponding
    target event. The resulting sequence is explanation-only and cannot make
    the original candidate pass the semantic gate.
    """
    if max_divergences < 1:
        raise ValueError("max_divergences must be positive")
    known_addresses = dict(target.symbol_addresses)
    for name, address in candidate.symbol_addresses.items():
        prior = known_addresses.get(name)
        if prior is not None and prior != address:
            raise ValueError(
                f"conflicting linker addresses for {name}: "
                f"{prior:#x} != {address:#x}")
        known_addresses[name] = address
    symbols = SymbolTable(
        target.symbols | candidate.symbols | _case_symbols(case),
        known_addresses)
    symbol_sizes = dict(target.symbol_sizes)
    for name, size in candidate.symbol_sizes.items():
        symbol_sizes[name] = max(symbol_sizes.get(name, 0), size)
    target_memory = _seed_memory(
        symbols, case, symbol_sizes, program=target,
        fallback_program=candidate)
    candidate_memory = _seed_memory(
        symbols, case, symbol_sizes, program=candidate,
        fallback_program=target)
    registers = _seed_registers(case)
    target_run = Runner(
        target, target_memory, registers, symbols,
        call_arities=call_arities, case_seed=case.seed,
        max_steps=max_steps, return_registers=return_registers,
        call_returns={(callee, ordinal): value
                      for callee, ordinal, value in case.call_returns},
        capture_call_checkpoints=True).execute()
    call_interventions: dict[int, CallEvent] = {}
    write_interventions: dict[int, WriteEvent] = {}
    observations: list[ResynchronizedObservation] = []
    used_actions: set[tuple[str, int]] = set()
    terminal = _compare_runs(case, target_run, target_run)
    stop_reason = "resynchronization budget exhausted"

    for stage in range(1, max_divergences + 2):
        candidate_run = Runner(
            candidate, candidate_memory.clone(), registers, symbols,
            call_arities=call_arities, case_seed=case.seed,
            max_steps=max_steps, return_registers=return_registers,
            call_returns={(callee, ordinal): value
                          for callee, ordinal, value in case.call_returns},
            call_interventions=call_interventions,
            write_interventions=write_interventions,
            capture_call_checkpoints=True).execute()
        terminal = _compare_runs(case, target_run, candidate_run)
        if terminal.status == "passed":
            stop_reason = "counterfactual checkpoints align all observables"
            break
        if stage > max_divergences:
            break
        observation, action = _resynchronization_observation(terminal, stage)
        observations.append(observation)
        if action is None:
            stop_reason = observation.intervention
            break
        action_kind, ordinal, target_event = action
        identity = (action_kind, ordinal)
        if identity in used_actions:
            stop_reason = "intervention did not advance the first divergence"
            break
        used_actions.add(identity)
        if action_kind == "call":
            call_interventions[ordinal] = target_event
        else:
            write_interventions[ordinal] = target_event

    return ResynchronizationReport(
        case.name, tuple(observations), terminal.status,
        terminal.first_divergence, stop_reason)


def run_resynchronized_suite(
        target_assembly: str, candidate_assembly: str,
        cases: tuple[TestCase, ...], *, target_name: str = "target",
        candidate_name: str = "candidate",
        call_arities: dict[str, int] | None = None,
        return_registers: tuple[str, ...] = (),
        max_divergences: int = 3) -> list[ResynchronizationReport]:
    target = Program.parse(target_name, target_assembly)
    candidate = Program.parse(candidate_name, candidate_assembly)
    return [resynchronized_report(
        target, candidate, case, call_arities=call_arities,
        return_registers=return_registers,
        max_divergences=max_divergences) for case in cases]


def render_divergence_bundle(
        reports: list[ResynchronizationReport], *,
        max_examples: int = 3) -> str:
    """Compress repeated cases into cross-case independent fault clusters."""
    clusters: dict[str, list[ResynchronizedObservation]] = {}
    for report in reports:
        for observation in report.observations:
            clusters.setdefault(observation.signature, []).append(observation)
    lines = [
        "RESYNCHRONIZED INDEPENDENT-DIVERGENCE BUNDLE",
        "Diagnostic counterfactual only: interventions never count as a pass.",
        ("Memory notation `address/width=value` uses a raw byte address and an "
         "access width in bytes; `/2` is not division or C pointer scaling."),
        f"cases={len(reports)}; independent fault clusters={len(clusters)}",
    ]
    if not clusters:
        lines.append("No failing semantic checkpoint was available to bundle.")
    for index, (signature, observations) in enumerate(clusters.items(), 1):
        case_names = sorted({row.case for row in observations})
        stages = sorted({row.stage for row in observations})
        lines.extend([
            "",
            f"CLUSTER {index}: {signature}",
            f"seen in {len(case_names)}/{len(reports)} cases; stages={stages}",
        ])
        for row in observations[:max_examples]:
            lines.append(
                f"  {row.case} stage {row.stage}: {row.first_divergence}")
        if len(observations) > max_examples:
            lines.append(
                f"  ... {len(observations) - max_examples} further example(s)")
        representative = observations[0]
        lines.append("representative causal evidence:")
        lines.extend("  " + part for part in representative.evidence.splitlines())
        lines.append("counterfactual action: " + representative.intervention)
    terminal_counts: dict[str, int] = {}
    for report in reports:
        terminal_counts[report.stop_reason] = \
            terminal_counts.get(report.stop_reason, 0) + 1
    lines.extend(["", "COUNTERFACTUAL TERMINATION:"])
    for reason, count in sorted(terminal_counts.items()):
        lines.append(f"- {count}/{len(reports)}: {reason}")
    return "\n".join(lines)


def run_suite(target_assembly: str, candidate_assembly: str,
              cases: tuple[TestCase, ...], *, target_name: str = "target",
              candidate_name: str = "candidate",
              call_arities: dict[str, int] | None = None,
              return_registers: tuple[str, ...] = (),
              max_steps: int = 10_000, callee_environment=None) -> list[DifferentialResult]:
    target = Program.parse(target_name, target_assembly)
    candidate = Program.parse(candidate_name, candidate_assembly)
    return [compare_programs(
        target, candidate, case, call_arities=call_arities,
        return_registers=return_registers, max_steps=max_steps, callee_environment=callee_environment)
        for case in cases]


@dataclass(frozen=True)
class CoverageReport:
    """Executed target coverage, kept separate from semantic agreement.

    An uncovered edge is not automatically feasible.  The report deliberately
    calls it unresolved until a generated case covers it or a separate proof
    marks it infeasible.
    """

    reachable_instructions: tuple[int, ...]
    covered_instructions: tuple[int, ...]
    conditional_branches: tuple[int, ...]
    covered_branch_edges: tuple[tuple[int, bool], ...]
    missing_instructions: tuple[tuple[int, str], ...]
    unresolved_branch_edges: tuple[tuple[int, bool], ...]
    total_runs: int
    completed_runs: int
    infeasible_branch_edges: tuple[tuple[int, bool], ...] = ()
    unresolved_indirect_jumps: tuple[int, ...] = ()

    @property
    def instruction_complete(self) -> bool:
        return not self.missing_instructions

    @property
    def branch_edge_complete(self) -> bool:
        return not (self.unresolved_branch_edges or self.unresolved_indirect_jumps)

    @property
    def complete(self) -> bool:
        return self.instruction_complete and self.branch_edge_complete

    def to_dict(self) -> dict:
        instruction_total = len(self.reachable_instructions)
        edge_total = (len(self.conditional_branches) * 2 -
                      len(self.infeasible_branch_edges))
        return {
            "status": "complete" if self.complete else "partial",
            "coverage_model_version": COVERAGE_MODEL_VERSION,
            "total_runs": self.total_runs,
            "completed_runs": self.completed_runs,
            "reachable_instruction_count": instruction_total,
            "covered_instruction_count": len(self.covered_instructions),
            "instruction_coverage": (
                len(self.covered_instructions) / instruction_total
                if instruction_total else 1.0),
            "conditional_branch_count": len(self.conditional_branches),
            "covered_branch_edge_count": len(self.covered_branch_edges),
            "branch_edge_coverage": (
                len(self.covered_branch_edges) / edge_total
                if edge_total else 1.0),
            "missing_instructions": [
                {"instruction": index, "text": text}
                for index, text in self.missing_instructions
            ],
            "unresolved_branch_edges": [
                {"instruction": index,
                 "outcome": "taken" if taken else "not_taken"}
                for index, taken in self.unresolved_branch_edges
            ],
            "infeasible_branch_edges": [
                {"instruction": index,
                 "outcome": "taken" if taken else "not_taken"}
                for index, taken in self.infeasible_branch_edges
            ],
            "unresolved_indirect_jumps": list(self.unresolved_indirect_jumps),
        }


@dataclass(frozen=True)
class CoverageExploration:
    cases: tuple[TestCase, ...]
    runs: tuple[RunResult, ...]
    report: CoverageReport
    attempted_cases: int
    stop_reason: str
    execution_obstructions: tuple[dict, ...] = ()
    trial_status_counts: dict[str, int] = field(default_factory=dict)
    noncompleted_trial_examples: tuple[dict, ...] = ()
    executed_steps: int = 0
    max_total_steps: int | None = None

    def to_dict(self) -> dict:
        return {
            "attempted_cases": self.attempted_cases,
            "selected_case_count": len(self.cases),
            "stop_reason": self.stop_reason,
            "coverage": self.report.to_dict(),
            "selected_cases": [asdict(case) for case in self.cases],
            "execution_obstructions": list(self.execution_obstructions),
            "trial_status_counts": dict(self.trial_status_counts),
            "noncompleted_trial_examples": list(self.noncompleted_trial_examples),
            "executed_steps": self.executed_steps,
            "max_total_steps": self.max_total_steps,
        }


@dataclass(frozen=True)
class SemanticStressPanel:
    """Boundary mutations retained for value discrimination, not coverage.

    Coverage-guided exploration intentionally throws away an input when it
    follows an already-covered path.  That is the wrong filter for checking
    arithmetic: two implementations can take every same branch and still
    compute different values.  This panel keeps a balanced sample from every
    observed memory/register input dimension whether or not it adds coverage.
    """

    cases: tuple[TestCase, ...]
    runs: tuple[RunResult, ...]
    generated_cases: int
    dimension_counts: tuple[tuple[str, int], ...]
    coverage: CoverageReport
    rejected_status_counts: tuple[tuple[str, int], ...] = ()
    attempted_cases: int = 0
    executed_steps: int = 0
    work_limits: dict = field(default_factory=dict)
    stop_reasons: tuple[str, ...] = ()
    examined_mutations: int = 0
    unattempted_seed_count: int = 0

    def to_dict(self) -> dict:
        return {
            "selected_case_count": len(self.cases),
            "generated_case_count": self.generated_cases,
            "dimension_counts": dict(self.dimension_counts),
            "target_run_statuses": {
                status: sum(run.status == status for run in self.runs)
                for status in sorted({run.status for run in self.runs})
            },
            "rejected_target_run_statuses": dict(
                self.rejected_status_counts),
            "target_coverage": self.coverage.to_dict(),
            "selected_cases": [asdict(case) for case in self.cases],
            "attempted_cases": self.attempted_cases,
            "executed_steps": self.executed_steps,
            "work_limits": self.work_limits,
            "stop_reasons": list(self.stop_reasons),
            "examined_mutations": self.examined_mutations,
            "unattempted_seed_count": self.unattempted_seed_count,
        }


def _indirect_jump_targets(program: Program, index: int) -> tuple[int, ...] | None:
    """Resolve a register jump loaded from an ELF-annotated switch table.

    Unknown targets remain unknown; a dynamic register jump is never silently
    treated as a return. The common IDO form loads the jump register directly
    with a relocation-bearing lw and has no intervening control transfer.
    """
    jump = program.instructions[index]
    register = jump.operands[0].strip().lstrip("$")
    # Hand-written/scaffold assembly can preserve entry ra in a callee-saved
    # register. Recognize only a unique entry-prefix copy with no backedge
    # into that prefix; calls preserve the destination under the runner ABI.
    definitions = [row for row in program.instructions
                   if _written_register(row) == register]
    if register in CALLEE_SAVED and register not in {"sp", "fp", "ra"} \
            and len(definitions) == 1:
        definition = definitions[0]
        if definition.opcode == "move" and definition.operands[1] == "ra" \
                and not any(cfg.has_delay_slot(row.opcode)
                            for row in program.instructions[:definition.index]):
            destinations = [target // 4 for target in program.data_words.values()]
            for row in program.instructions:
                if cfg.is_conditional_branch(row.opcode) or row.opcode in {"b", "j"}:
                    destinations.append(program.branch_target(row.operands[-1]))
            other_unknown_jump = any(
                row.opcode == "jr" and row.operands[0].strip().lstrip("$")
                not in {"ra", register} for row in program.instructions)
            if not other_unknown_jump and not any(
                    destination <= definition.index for destination in destinations):
                return ()
    for prior in reversed(program.instructions[:index]):
        if cfg.has_delay_slot(prior.opcode):
            return None
        if _written_register(prior) != register:
            continue
        if prior.opcode != "lw" or len(prior.operands) != 2:
            return None
        symbol = re.search(r"%lo\(([^)]+)\)", prior.operands[1])
        if symbol is None:
            return None
        name, addend = _parse_symbol_expression(symbol.group(1))
        if addend:
            return None
        words = sorted((offset, target) for (key, offset), target
                       in program.data_words.items() if key == name)
        if not words or [offset for offset, _ in words] != \
                list(range(0, 4 * len(words), 4)):
            return None
        if any(target % 4 or not 0 <= target // 4 < len(program.instructions)
               for _, target in words):
            return None
        return tuple(sorted({target // 4 for _, target in words}))
    return None


def _static_coverage_targets(
        program: Program, infeasible_edges: set[tuple[int, bool]] | None = None
        ) -> tuple[set[int], set[tuple[int, bool]]]:
    """Return structurally reachable instructions and conditional edges.

    Delay slots are marked reachable with their controlling transfer rather
    than treated as ordinary fallthrough instructions.
    """
    infeasible_edges = infeasible_edges or set()
    reachable: set[int] = set()
    edges: set[tuple[int, bool]] = set()
    heads = [0]
    visited_heads: set[int] = set()
    size = len(program.instructions)
    while heads:
        index = heads.pop()
        if index in visited_heads or not 0 <= index < size:
            continue
        visited_heads.add(index)
        instruction = program.instructions[index]
        reachable.add(index)
        op = instruction.opcode
        if cfg.has_delay_slot(op):
            if index + 1 < size:
                reachable.add(index + 1)
        if cfg.is_conditional_branch(op):
            if (index, True) not in infeasible_edges:
                edges.add((index, True))
                heads.append(program.branch_target(instruction.operands[-1]))
            if (index, False) not in infeasible_edges:
                edges.add((index, False))
                heads.append(index + 2)
        elif op in {"b", "j"}:
            heads.append(program.branch_target(instruction.operands[0]))
        elif op == "jr":
            if instruction.operands[0].strip().lstrip("$") != "ra":
                targets = _indirect_jump_targets(program, index)
                # Conservatively include the whole function when its target
                # set is unknown. The report also retains an unresolved jump.
                heads.extend(targets if targets is not None else range(size))
            continue
        elif op == "jal":
            heads.append(index + 2)
        else:
            heads.append(index + 1)
    return reachable, edges


_DESTINATION_OPS = frozenset({
    "move", "li", "lui", "addiu", "addi", "addu", "add", "subu",
    "sub", "or", "and", "xor", "ori", "andi", "xori", "sll", "srl",
    "sra", "sllv", "srlv", "srav", "negu", "slti", "sltiu", "slt",
    "sltu", "mflo", "mfhi",
    "lb", "lbu", "lh", "lhu", "lw", "lwc1", "mfc1", "cfc1",
})


def _written_register(instruction: cfg.Instruction) -> str | None:
    if instruction.opcode not in _DESTINATION_OPS or not instruction.operands:
        return None
    return instruction.operands[0].strip().lstrip("$")


def _proven_infeasible_division_edges(
        program: Program) -> tuple[set[tuple[int, bool]], set[int]]:
    """Prove the negative arm of IDO's signed power-of-two division idiom.

    This deliberately recognizes only a loop register initialized to zero and
    changed solely by positive self-increments before a backward loop edge.
    The skipped correction block is then unreachable rather than merely absent
    from the generated test panel.
    """
    edges: set[tuple[int, bool]] = set()
    unreachable: set[int] = set()
    instructions = program.instructions
    for index, branch in enumerate(instructions[:-3]):
        if branch.opcode != "bgez" or len(branch.operands) != 2:
            continue
        register = branch.operands[0].strip().lstrip("$")
        try:
            target = program.branch_target(branch.operands[1])
        except UnsupportedInstruction:
            continue
        if target != index + 4:
            continue
        correction = instructions[index + 2:index + 4]
        if correction[0].opcode != "addiu" or \
                len(correction[0].operands) != 3 or \
                correction[0].operands[1].strip().lstrip("$") != register:
            continue
        try:
            addend = int(correction[0].operands[2], 0)
        except ValueError:
            continue
        if addend <= 0 or correction[1].opcode != "sra":
            continue

        initializer = None
        for prior in reversed(instructions[:index]):
            if _written_register(prior) == register:
                initializer = prior
                break
        if initializer is None or not (
                initializer.opcode == "move" and
                len(initializer.operands) == 2 and
                initializer.operands[1].strip().lstrip("$") == "zero"):
            continue
        init_index = initializer.index
        latch = None
        for later in instructions[index + 4:]:
            if not cfg.is_conditional_branch(later.opcode) and \
                    later.opcode not in {"b", "j"}:
                continue
            try:
                destination = program.branch_target(later.operands[-1])
            except (UnsupportedInstruction, IndexError):
                continue
            if init_index <= destination <= index:
                latch = later.index
                break
        if latch is None:
            continue
        safe = True
        for instruction in instructions[init_index + 1:latch + 1]:
            if _written_register(instruction) != register:
                continue
            if instruction.opcode != "addiu" or \
                    len(instruction.operands) != 3 or \
                    instruction.operands[1].strip().lstrip("$") != register:
                safe = False
                break
            try:
                if int(instruction.operands[2], 0) <= 0:
                    safe = False
                    break
            except ValueError:
                safe = False
                break
        if safe:
            edges.add((index, False))
            unreachable.update((index + 2, index + 3))
    return edges, unreachable


def _literal_register_write(instruction: cfg.Instruction,
                            register: str) -> int | None:
    """Return a constant written by one deliberately small MIPS idiom."""
    operands = instruction.operands
    if not operands or operands[0].strip().lstrip("$") != register:
        return None
    try:
        if instruction.opcode == "li" and len(operands) == 2:
            return u32(int(operands[1], 0))
        if instruction.opcode == "lui" and len(operands) == 2:
            return u32(int(operands[1], 0) << 16)
        if instruction.opcode == "move" and len(operands) == 2 and \
                operands[1].strip().lstrip("$") == "zero":
            return 0
        if instruction.opcode in {"addiu", "addi"} and len(operands) == 3 \
                and operands[1].strip().lstrip("$") == "zero":
            return u32(sign_extend(int(operands[2], 0), 16))
        if instruction.opcode in {"ori", "xori"} and len(operands) == 3 and \
                operands[1].strip().lstrip("$") == "zero":
            return int(operands[2], 0) & 0xFFFF
    except ValueError:
        return None
    return None


def _proven_constant_before(program: Program, index: int,
                            register: str) -> int | None:
    """Conservatively recover a dominating literal register definition."""
    register = register.strip().lstrip("$")
    if register == "zero":
        return 0
    writes = [instruction for instruction in program.instructions[:index]
              if _written_register(instruction) == register]
    if not writes:
        return None
    latest = writes[-1]
    value = _literal_register_write(latest, register)
    if value is None:
        return None

    # A literal in the current straight-line block is authoritative unless a
    # branch can enter between the definition and use.
    transfers = [instruction for instruction in
                 program.instructions[latest.index + 1:index]
                 if cfg.has_delay_slot(instruction.opcode)]
    branch_targets = set()
    for instruction in program.instructions:
        if not (cfg.is_conditional_branch(instruction.opcode) or
                instruction.opcode in {"b", "j"}):
            continue
        try:
            branch_targets.add(program.branch_target(instruction.operands[-1]))
        except (UnsupportedInstruction, IndexError):
            pass
    has_indirect_jump = any(
        row.opcode == "jr" and row.operands[0].strip().lstrip("$") != "ra"
        for row in program.instructions)
    if not transfers and not has_indirect_jump and not any(
            latest.index < target <= index for target in branch_targets):
        return value

    # A sole literal definition in the entry straight-line prefix dominates
    # every later use.  This captures IDO's constant-divisor guard sequences
    # without pretending to solve arbitrary path-dependent constants.
    if _instruction_dominates(program, latest.index, index):
        return value
    return None


def _instruction_dominates(program: Program, definition: int,
                           use: int) -> bool:
    """Return whether every structural path to ``use`` executes ``definition``.

    Delay slots are traversed with their controlling transfer so blocking a
    delay-slot definition also blocks the transfer's successors.
    """
    heads = [0]
    visited: set[int] = set()
    size = len(program.instructions)
    while heads:
        index = heads.pop()
        if index in visited or not 0 <= index < size or index == definition:
            continue
        visited.add(index)
        if index == use:
            return False
        instruction = program.instructions[index]
        op = instruction.opcode
        if cfg.has_delay_slot(op):
            delay = index + 1
            if delay == definition:
                continue
            if delay == use:
                return False
        if cfg.is_conditional_branch(op):
            heads.append(program.branch_target(instruction.operands[-1]))
            heads.append(index + 2)
        elif op in {"b", "j"}:
            heads.append(program.branch_target(instruction.operands[0]))
        elif op == "jr":
            if instruction.operands[0].strip().lstrip("$") != "ra":
                targets = _indirect_jump_targets(program, index)
                heads.extend(targets if targets is not None else range(size))
            continue
        elif op == "jal":
            heads.append(index + 2)
        else:
            heads.append(index + 1)
    return True


def _proven_constant_branch_edges(program: Program) -> set[tuple[int, bool]]:
    """Prove impossible outcomes of branches whose operands are constants."""
    infeasible: set[tuple[int, bool]] = set()
    for instruction in program.instructions:
        op, operands = instruction.opcode, instruction.operands
        taken: bool | None = None
        if op in {"beq", "bne"} and len(operands) == 3:
            left = _proven_constant_before(
                program, instruction.index, operands[0])
            right = _proven_constant_before(
                program, instruction.index, operands[1])
            if left is not None and right is not None:
                taken = left == right if op == "beq" else left != right
        elif op in {"beqz", "bnez"} and len(operands) == 2:
            value = _proven_constant_before(
                program, instruction.index, operands[0])
            if value is not None:
                taken = value == 0 if op == "beqz" else value != 0
        if taken is not None:
            infeasible.add((instruction.index, not taken))
    return infeasible


def coverage_report(program: Program, runs: list[RunResult] |
                    tuple[RunResult, ...]) -> CoverageReport:
    infeasible_edges, infeasible_instructions = \
        _proven_infeasible_division_edges(program)
    infeasible_edges |= _proven_constant_branch_edges(program)
    reachable, expected_edges = _static_coverage_targets(
        program, infeasible_edges)
    reachable -= infeasible_instructions
    covered: set[int] = set()
    covered_edges: set[tuple[int, bool]] = set()
    completed = [run for run in runs if run.status in COMPLETED_STATUSES]
    for run in completed:
        for event in run.trace:
            covered.add(event.instruction)
            if event.effect.startswith("branch taken;"):
                covered_edges.add((event.instruction, True))
            elif event.effect.startswith("branch not taken;"):
                covered_edges.add((event.instruction, False))
    covered &= reachable
    missing = tuple(
        (index, program.instructions[index].text)
        for index in sorted(reachable - covered)
    )
    conditional = tuple(sorted({index for index, _taken in expected_edges}))
    return CoverageReport(
        reachable_instructions=tuple(sorted(reachable)),
        covered_instructions=tuple(sorted(covered)),
        conditional_branches=conditional,
        covered_branch_edges=tuple(sorted(covered_edges & expected_edges)),
        missing_instructions=missing,
        unresolved_branch_edges=tuple(sorted(expected_edges - covered_edges)),
        total_runs=len(runs), completed_runs=len(completed),
        infeasible_branch_edges=tuple(sorted(infeasible_edges)),
        unresolved_indirect_jumps=tuple(
            index for index in sorted(reachable)
            if program.instructions[index].opcode == "jr"
            and program.instructions[index].operands[0].strip().lstrip("$") != "ra"
            and _indirect_jump_targets(program, index) is None),
    )


def execute_case(program: Program, case: TestCase, *,
                 call_arities: dict[str, int] | None = None,
                 return_registers: tuple[str, ...] = (),
                 max_steps: int = 10_000, callee_environment=None) -> RunResult:
    """Execute one side for target-led path discovery."""
    symbols, sizes, dependencies = _execution_context((program,),case,callee_environment)
    return Runner(
        program, _seed_memory(
            symbols, case, sizes, program=program, dependencies=dependencies),
        _seed_registers(case), symbols,
        call_arities=call_arities, case_seed=case.seed,
        max_steps=max_steps, return_registers=return_registers, callee_environment=callee_environment,
        call_returns={(callee, ordinal): value
                      for callee, ordinal, value in case.call_returns}).execute()


_LOAD_EFFECT = re.compile(r"^load ([^/]+)/([124])=")
_CALL_RETURN_ORIGIN = re.compile(
    r"return from ([A-Za-z_][\w.$]*) call #(\d+)")


def _read_locations(run: RunResult) -> set[tuple[str, str | int, int]]:
    locations: set[tuple[str, str | int, int]] = set()
    for call in run.concrete_calls:
        locations.update(tuple(location) for location in call.get('input_read_locations',()))
    for event in run.trace:
        match = _LOAD_EFFECT.match(event.effect)
        if not match:
            continue
        pointer, width_text = match.groups()
        width = int(width_text)
        player = re.fullmatch(r"player(?:\+0x([0-9a-f]+))?", pointer)
        if player:
            locations.add(("player", int(player.group(1) or "0", 16), width))
            continue
        if re.fullmatch(r"arg(?:[1-9]|1[0-5])(?:\+0x[0-9a-f]+)?", pointer):
            locations.add(("global", "@" + pointer, width))
            continue
        global_name = re.fullmatch(
            r"&([A-Za-z_][\w.$]*(?:\+0x[0-9a-f]+)?)", pointer)
        if global_name:
            locations.add(("global", global_name.group(1), width))
    return locations


def _literal_values(program: Program) -> set[int]:
    values: set[int] = set()
    immediate_positions = {
        "li": (1,), "addiu": (2,), "addi": (2,),
        "ori": (2,), "andi": (2,), "xori": (2,),
        "slti": (2,), "sltiu": (2,),
    }
    for instruction in program.instructions:
        for index in immediate_positions.get(instruction.opcode, ()):
            if index >= len(instruction.operands):
                continue
            try:
                values.add(int(instruction.operands[index], 0))
            except ValueError:
                pass
    return values


def _boundary_values(program: Program, width: int) -> tuple[int, ...]:
    bits = width * 8
    mask = (1 << bits) - 1
    sign = 1 << (bits - 1)
    values = {0, 1, 2, mask, mask - 1, sign, sign - 1}
    # Bit-mask state machines (RNGs, flags, packed controllers) often require
    # one particular bit before one or more shifts. Numeric min/max boundaries
    # cover neither that basis nor the inverse image of a later mask. A
    # one-hot basis is bounded (32 cases at word width) and lets breadth-first
    # exploration discover those paths without solving arbitrary arithmetic.
    values.update(1 << bit for bit in range(bits))
    # Small switches need every selector, not just numeric/bit boundaries.
    # Relocation-derived jump-table extents are binary evidence; this does not
    # guess which argument is the selector or claim larger tables are covered.
    table_sizes: dict[str, int] = {}
    for table, _offset in program.data_words:
        table_sizes[table] = table_sizes.get(table, 0) + 1
    values.update(range(min(mask + 1, 256, max(table_sizes.values(), default=0))))
    if width == 4:
        # A word loaded from memory may itself be a pointer.  The mapped
        # player/scratch regions are deterministic valid pointees for target-
        # led exploration; scalar uses simply receive a few more boundary
        # words.  Independent scratch bases matter: using PLAYER_BASE as a
        # display-list buffer can overwrite the input object and fabricate a
        # later path failure in long render functions.
        values.add(PLAYER_BASE)
        values.update(ARG_POINTER_BASES.values())
    for literal in _literal_values(program):
        literal &= mask
        values.update((literal, (literal - 1) & mask, (literal + 1) & mask))
    return tuple(sorted(values))


def _replace_register(case: TestCase, name: str, value: int,
                      suffix: str) -> TestCase:
    registers = tuple((key, old) for key, old in case.entry_registers
                      if key.strip().lstrip("$") != name)
    return TestCase(
        f"{case.name}-{suffix}", case.seed, case.player_writes,
        case.global_writes, registers + ((name, value),), case.call_returns)


def _replace_call_returns(
        case: TestCase, replacements: tuple[tuple[str, int, int], ...],
        suffix: str) -> TestCase:
    values = {(callee, ordinal): value
              for callee, ordinal, value in case.call_returns}
    values.update({(callee, ordinal): u32(value)
                   for callee, ordinal, value in replacements})
    return TestCase(
        f"{case.name}-{suffix}", case.seed, case.player_writes,
        case.global_writes, case.entry_registers,
        tuple((callee, ordinal, value)
              for (callee, ordinal), value in sorted(values.items())))


def _iter_mutations(program: Program, case: TestCase, run: RunResult, *,
               mutable_entry_registers: tuple[str, ...],
               pointer_entry_registers: tuple[str, ...] = (), category=None
               ):
    locations = sorted(
        _read_locations(run) if category in (None,'memory') else (),
        key=lambda row: (row[0], str(row[1]), row[2]))
    urgent = _failure_input_location(run)
    if urgent in locations:
        locations.remove(urgent)
        locations.insert(0, urgent)
    for kind, location, width in locations:
        values = _boundary_values(program, width)
        if (kind, location, width) == urgent and width == 4:
            # A word that directly supplied an unmapped effective address is
            # a pointer, not an arbitrary scalar.  Try independent scratch
            # regions before PLAYER_BASE so an output buffer cannot overwrite
            # the object whose later fields control this same path.
            pointer_values = tuple(ARG_POINTER_BASES[name]
                                   for name in ("a1", "a2", "a3"))
            preferred = pointer_values + (PLAYER_BASE,)
            values = preferred + tuple(
                value for value in values if value not in preferred)
        for value in values:
            if kind == "player":
                writes = case.player_writes + ((int(location), width, value),)
                yield TestCase(
                    f"{case.name}-p{int(location):x}w{width}v{value:x}",
                    case.seed, writes, case.global_writes,
                    case.entry_registers, case.call_returns)
            else:
                writes = case.global_writes + ((str(location), width, value),)
                yield TestCase(
                    f"{case.name}-g{location}w{width}v{value:x}",
                    case.seed, case.player_writes, writes,
                    case.entry_registers, case.call_returns)
    for name in (mutable_entry_registers if category in (None,'scalar') else ()):
        for value in _boundary_values(program, 4):
            yield _replace_register(
                case, name, value, f"r{name}v{value:x}")
    # Pointer arguments need alias cases, not merely scalar boundary values.
    # A load through a1 can observe a preceding write through a0 only when a1
    # points at the exact written byte. Coverage is unchanged in that case, but
    # semantics are not, so branch-only exploration cannot discover it.
    alias_values = {PLAYER_BASE, *ARG_POINTER_BASES.values()}
    for write in (run.writes if category in (None,'pointer') else ()):
        pointer = _pointer_parts(write.address)
        if pointer and pointer[0] == "player":
            alias_values.add(PLAYER_BASE + pointer[1])
    for name in (pointer_entry_registers if category in (None,'pointer') else ()):
        for value in sorted(alias_values):
            yield _replace_register(
                case, name, value, f"p{name}alias{value:x}")
    # Opaque callees are environmental inputs too.  Exercise each observed
    # return independently and all repeated calls to one callee jointly; the
    # latter reaches accumulator exits such as "none of four players hit".
    calls = {(event.callee, event.ordinal) for event in run.calls} if category in (None,'call') else set()
    faulting_call = _failure_call_return(run)
    for callee, ordinal in sorted(calls):
        values = (0, 1, MASK32)
        if faulting_call == (callee, ordinal):
            pointers = tuple(ARG_POINTER_BASES[name]
                             for name in ("a1", "a2", "a3"))
            values = pointers + values
        for value in values:
            yield _replace_call_returns(
                case, ((callee, ordinal, value),),
                f"c{callee}{ordinal}v{value:x}")
    callees = sorted({callee for callee, _ordinal in calls})
    for callee in callees:
        ordinals = sorted(ordinal for name, ordinal in calls
                          if name == callee)
        if len(ordinals) < 2:
            continue
        for value in (0, 1):
            yield _replace_call_returns(
                case, tuple((callee, ordinal, value)
                            for ordinal in ordinals),
                f"c{callee}allv{value:x}")
    # Random filler remains a separate input dimension for memory and normal
    # (non-overridden) opaque-call values.
    for delta in (range(1, 9) if category in (None,'filler') else ()):
        yield TestCase(
            f"{case.name}-seed{delta}", u32(case.seed + delta * 0x9E37),
            case.player_writes, case.global_writes, case.entry_registers,
            case.call_returns)


def _mutations(program, case, run, **kwargs):
    """Compatibility materialization for exploration callers."""
    return tuple(_iter_mutations(program, case, run, **kwargs))


def _balanced_mutations(program, seeds, runs, **kwargs):
    """Round-robin construction across input families and target seed cases.

    A finite prefix still omits dimensions within families. It must not spend
    its entire generation budget materializing one seed's memory mutations.
    """
    streams = deque((index, _iter_mutations(program,case,run,category=category,**kwargs))
        for category in ('memory','scalar','pointer','call','filler')
        for index,(case,run) in enumerate(zip(seeds,runs)))
    while streams:
        index, stream = streams.popleft()
        mutation = next(stream, None)
        if mutation is not None:
            yield index, mutation
            streams.append((index,stream))


def _failure_call_return(run: RunResult) -> tuple[str, int] | None:
    """Recover an opaque call return that supplied a faulting pointer."""
    texts = [run.error]
    if run.trace:
        texts.append(run.trace[-1].effect)
    for text in texts:
        matches = list(_CALL_RETURN_ORIGIN.finditer(text))
        if matches:
            callee, ordinal = matches[-1].groups()
            return callee, int(ordinal)
    return None


def _failure_input_location(run: RunResult) -> tuple[str, int | str, int] | None:
    """Recover the load that supplied a faulting effective address.

    A prefix may have read dozens of fields before it faults.  Mutating those
    fields alphabetically throws away the strongest gradient in the trace: the
    fault message names the load that produced the bad pointer.  Prefer that
    input while retaining every other boundary mutation behind it.
    """
    texts = [run.error]
    if run.trace:
        texts.append(run.trace[-1].effect)
    for text in texts:
        matches = list(_LOAD_ORIGIN.finditer(text))
        if not matches:
            continue
        pointer, width_text = matches[-1].groups()
        width = int(width_text)
        player = re.fullmatch(r"player(?:\+0x([0-9a-f]+))?", pointer)
        if player:
            return "player", int(player.group(1) or "0", 16), width
        return "global", ("@" + pointer if pointer.startswith("arg") else pointer[1:]), width
    return None


def _mutation_dimension(base: TestCase, mutation: TestCase) -> str:
    """Name the independently varied input dimension of one mutation."""
    if len(mutation.player_writes) > len(base.player_writes):
        offset, width, _value = mutation.player_writes[-1]
        return f"player+0x{offset:x}/{width}"
    if len(mutation.global_writes) > len(base.global_writes):
        name, width, _value = mutation.global_writes[-1]
        return f"&{name}/{width}"
    base_registers = dict(base.entry_registers)
    for name, value in mutation.entry_registers:
        if base_registers.get(name) != value:
            return f"register:{name}"
    if mutation.call_returns != base.call_returns:
        base_returns = {(callee, ordinal): value
                        for callee, ordinal, value in base.call_returns}
        changed = [(callee, ordinal) for callee, ordinal, value
                   in mutation.call_returns
                   if base_returns.get((callee, ordinal)) != value]
        if len(changed) == 1:
            return f"call:{changed[0][0]}#{changed[0][1]}"
        return "call-group:" + ",".join(
            f"{callee}#{ordinal}" for callee, ordinal in changed)
    if mutation.seed != base.seed:
        return "seed"
    return "duplicate"


_LOAD_ORIGIN = re.compile(
    r"\bi\d+\s+\w+\s+"
    r"((?:player|arg(?:[1-9]|1[0-5]))(?:\+0x[0-9a-f]+)?|"
    r"&[A-Za-z_][\w.$]*(?:\+0x[0-9a-f]+)?)/([124])\s+->")


def _mutate_loaded_origin(case: TestCase, origin: str, value: int,
                          suffix: str) -> TestCase | None:
    """Drive a branch operand back through a traced load to one test input."""
    match = _LOAD_ORIGIN.search(origin)
    if not match:
        return None
    pointer, width_text = match.groups()
    width = int(width_text)
    value &= (1 << (width * 8)) - 1
    player = re.fullmatch(r"player(?:\+0x([0-9a-f]+))?", pointer)
    if player:
        offset = int(player.group(1) or "0", 16)
        return TestCase(
            f"{case.name}-{suffix}-p{offset:x}w{width}v{value:x}",
            case.seed, case.player_writes + ((offset, width, value),),
            case.global_writes, case.entry_registers, case.call_returns)
    global_name = "@" + pointer if pointer.startswith("arg") else pointer[1:]
    return TestCase(
        f"{case.name}-{suffix}-g{global_name}w{width}v{value:x}",
        case.seed, case.player_writes,
        case.global_writes + ((global_name, width, value),),
        case.entry_registers, case.call_returns)


_SLTI_ORIGIN = re.compile(
    r"\bi\d+\s+(sltiu?)\((.+),\s*(-?(?:0x[0-9a-fA-F]+|\d+))\)\s+->")


def _invert_slti_origin(origin: str, wants_nonzero: bool
                        ) -> tuple[str, int] | None:
    """Choose a source value that makes a traced slti/sltiu boolean true/false."""
    match = _SLTI_ORIGIN.search(origin)
    if match is None:
        return None
    opcode, source_origin, immediate_text = match.groups()
    immediate = sign_extend(int(immediate_text, 0) & 0xFFFF, 16)
    if opcode == "sltiu":
        immediate = u32(immediate)
        if wants_nonzero and immediate == 0:
            return None
    return source_origin, immediate - 1 if wants_nonzero else immediate


def _effective_input_origin(
        run: RunResult, origin: str, before_trace_position: int) -> str:
    """Follow a loaded value through the last persistent store that made it."""
    current = origin
    position = before_trace_position
    visited: set[tuple[str, int]] = set()
    while True:
        match = _LOAD_ORIGIN.search(current)
        if match is None:
            return current
        pointer = match.group(1)
        key = (pointer, position)
        if key in visited:
            return current
        visited.add(key)
        writes = [write for write in run.writes
                  if write.address == pointer and
                  write.trace_position < position]
        if not writes:
            return current
        latest = max(writes, key=lambda write: write.trace_position)
        current = latest.value_provenance
        position = latest.trace_position


def _mutate_call_origin(case: TestCase, origin: str, value: int,
                        suffix: str) -> TestCase | None:
    match = _CALL_RETURN_ORIGIN.search(origin)
    if match is None:
        return None
    return _replace_call_returns(
        case, ((match.group(1), int(match.group(2)), value),), suffix)


def _predicate_mutations(program: Program, case: TestCase, run: RunResult,
                         unresolved_edges: tuple[tuple[int, bool], ...], *,
                         mutable_entry_registers: tuple[str, ...] = ("a1", "a2", "a3")
                         ) -> tuple[TestCase, ...]:
    """Solve simple observed branch predicates back to loaded input bytes.

    Boundary enumeration is deliberately broad but has no notion of why a
    path was missed.  For an executed integer branch, the trace already binds
    each register to its last load.  Equality and zero/sign predicates can be
    inverted exactly, producing a joint mutation on the path-bearing case.
    More complex derived predicates remain for the ordinary boundary search.
    """
    events = {event.instruction: event for event in run.trace
              if event.effect.startswith("branch ")}
    out: list[TestCase] = []
    for instruction_index, desired_taken in unresolved_edges:
        event = events.get(instruction_index)
        if event is None:
            continue
        instruction = program.instructions[instruction_index]
        op = instruction.opcode.rstrip("l") \
            if instruction.branch_likely else instruction.opcode
        reads = list(event.reads)
        proposals: list[tuple[str, int]] = []
        if op in {"beq", "bne"} and len(reads) >= 2:
            equal = desired_taken == (op == "beq")
            for index in range(2):
                other = reads[1 - index][1]
                proposals.append((reads[index][2], other if equal else
                                  u32(other ^ 1)))
        elif op in {"beqz", "bnez"} and reads:
            zero = desired_taken == (op == "beqz")
            # Register-register comparisons often feed the branch boolean.
            # Use the actual producer event, not a guessed textual expression.
            compare_origin = re.match(r"i(\d+) (sltu?)\(", reads[0][2])
            if compare_origin:
                producer = next((row for row in reversed(run.trace[:event.ordinal])
                                 if row.instruction == int(compare_origin.group(1))), None)
                if producer and len(producer.reads) == 2:
                    left, right = producer.reads
                    signed = compare_origin.group(2) == "slt"
                    minimum, maximum = (-0x80000000, 0x7fffffff) if signed else (0, MASK32)
                    lhs = sign_extend(left[1], 32) if signed else left[1]
                    rhs = sign_extend(right[1], 32) if signed else right[1]
                    if zero:
                        proposals.extend(((left[2], right[1]), (right[2], left[1])))
                    else:
                        if rhs > minimum:
                            proposals.append((left[2], u32(rhs - 1)))
                        if lhs < maximum:
                            proposals.append((right[2], u32(lhs + 1)))
            inverted = _invert_slti_origin(
                reads[0][2], wants_nonzero=not zero)
            proposals.append(
                inverted if inverted is not None else
                (reads[0][2], 0 if zero else 1))
        elif op in {"bgez", "bgtz", "blez", "bltz"} and reads:
            values = {
                ("bgez", True): 0, ("bgez", False): MASK32,
                ("bgtz", True): 1, ("bgtz", False): 0,
                ("blez", True): 0, ("blez", False): 1,
                ("bltz", True): MASK32, ("bltz", False): 0,
            }
            proposals.append((reads[0][2], values[(op, desired_taken)]))
        for origin, value in proposals:
            effective_origin = _effective_input_origin(
                run, origin, event.ordinal)
            mutation = _mutate_loaded_origin(
                case, effective_origin, value,
                f"edge{instruction_index}{'t' if desired_taken else 'f'}")
            if mutation is None:
                mutation = _mutate_call_origin(
                    case, effective_origin, value,
                    f"edge{instruction_index}"
                    f"{'t' if desired_taken else 'f'}")
            if mutation is None:
                entry = re.fullmatch(r"entry (a[0-3])=[^ ]+", effective_origin)
                if entry and entry.group(1) in mutable_entry_registers:
                    mutation = _replace_register(case, entry.group(1), value,
                                                 f"edge{instruction_index}-entry")
            if mutation is not None:
                out.append(mutation)
    return tuple(out)


def _stress_input_identity(case: TestCase) -> tuple:
    """Ignore fully shadowed writes without changing serialized evidence.

    Mutations append writes, so a second assignment to the same input used
    to consume another panel slot even when its final memory was identical.
    Only earlier writes with the EXACT same location and width are removed.
    Preserve the order of all survivors: overlapping widths and linker alias
    names can interact, and their ordering must not be normalized away.
    """
    def live_writes(writes):
        seen = set()
        live = []
        for location, width, value in reversed(writes):
            slot = (location, width)
            if slot not in seen:
                seen.add(slot)
                live.append((location, width, value))
        return tuple(reversed(live))

    return (case.seed, live_writes(case.player_writes),
            live_writes(case.global_writes), case.entry_registers,
            case.call_returns)


def build_semantic_stress_panel(
        target_assembly: str, seed_cases: tuple[TestCase, ...], *,
        target_name: str = "target",
        call_arities: dict[str, int] | None = None,
        return_registers: tuple[str, ...] = (),
        mutable_entry_registers: tuple[str, ...] = ("a1", "a2", "a3"),
        pointer_entry_registers: tuple[str, ...] = (),
        max_cases: int = 512,
        max_steps: int = 10_000, callee_environment=None,
        max_total_steps: int | None = None,
        max_trials: int | None = None,
        max_generated: int | None = None) -> SemanticStressPanel:
    """Build a deterministic, dimension-balanced semantic holdout panel.

    Each seed is executed on the target to discover the memory locations it
    actually reads.  Boundary values are then generated for every such load,
    mutable scalar argument, and the deterministic filler seed.  Selection is
    round-robin across both dimensions and base cases so a budget cannot be
    consumed by the first field encountered.  No candidate behavior is used
    to choose the panel.
    """
    if not seed_cases:
        raise ValueError("semantic stress panel requires at least one seed case")
    if max_cases < len(seed_cases):
        raise ValueError("semantic stress budget must retain every seed case")
    for limit in (max_total_steps, max_trials, max_generated):
        if limit is not None and (type(limit) is not int or limit <= 0):
            raise ValueError('stress work limits must be positive integers')
    program = Program.parse(target_name, target_assembly)
    attempted = executed = 0
    stops = []

    def execute(case):
        nonlocal attempted, executed
        if max_trials is not None and attempted >= max_trials:
            if 'trial budget exhausted' not in stops:
                stops.append('trial budget exhausted')
            return None
        remaining = max_steps if max_total_steps is None else max_total_steps-executed
        if remaining <= 0:
            if 'instruction budget exhausted' not in stops:
                stops.append('instruction budget exhausted')
            return None
        run = execute_case(program, case, call_arities=call_arities,
            return_registers=return_registers, max_steps=min(max_steps, remaining),
            callee_environment=callee_environment)
        attempted += 1
        executed += run.instruction_count
        return run

    def identity(case: TestCase) -> tuple:
        return _stress_input_identity(case)

    seed_runs = []
    for case in seed_cases:
        run = execute(case)
        if run is None:
            break
        seed_runs.append(run)
    selected = [case for case, run in zip(seed_cases, seed_runs)
                if run.status in COMPLETED_STATUSES]
    selected_runs = [run for run in seed_runs
                     if run.status in COMPLETED_STATUSES]
    seen = {identity(case) for case in seed_cases}
    groups: dict[tuple[str, int], list[TestCase]] = {}
    generated = 0
    examined = 0
    arguments = dict(mutable_entry_registers=mutable_entry_registers,
                     pointer_entry_registers=pointer_entry_registers)
    # Preserve historical unlimited generation order; bounded callers need
    # fair construction BEFORE truncation, not just fair execution afterward.
    mutations = (_balanced_mutations(program,seed_cases,seed_runs,**arguments)
        if max_generated is not None else
        ((index,mutation) for index,(case,run) in enumerate(zip(seed_cases,seed_runs))
         for mutation in _iter_mutations(program,case,run,**arguments)))
    while max_generated is None or examined < max_generated:
        item = next(mutations, None)
        if item is None:
            break
        seed_index, mutation = item
        examined += 1
        key = identity(mutation)
        if key in seen:
            continue
        seen.add(key)
        dimension = _mutation_dimension(seed_cases[seed_index], mutation)
        if dimension == "duplicate":
            continue
        generated += 1
        groups.setdefault((dimension, seed_index), []).append(mutation)
    if max_generated is not None and examined >= max_generated:
        stops.append('mutation generation budget reached; remaining dimensions unenumerated')

    # A cursor avoids destructive queue operations and makes the exact order
    # stable for receipts and regression tests.
    cursors = {key: 0 for key in groups}
    ordered_groups = sorted(groups)
    selected_dimensions: dict[str, int] = {}
    rejected_statuses: dict[str, int] = {}
    for run in seed_runs:
        if run.status not in COMPLETED_STATUSES:
            rejected_statuses[run.status] = \
                rejected_statuses.get(run.status, 0) + 1
    exhausted = False
    while len(selected) < max_cases and not exhausted:
        advanced = False
        for group in ordered_groups:
            cursor = cursors[group]
            rows = groups[group]
            if cursor >= len(rows):
                continue
            mutation = rows[cursor]
            cursors[group] = cursor + 1
            advanced = True
            run = execute(mutation)
            if run is None:
                exhausted = True
                break
            if run.status not in COMPLETED_STATUSES:
                rejected_statuses[run.status] = \
                    rejected_statuses.get(run.status, 0) + 1
                continue
            selected.append(mutation)
            selected_runs.append(run)
            selected_dimensions[group[0]] = \
                selected_dimensions.get(group[0], 0) + 1
            if len(selected) >= max_cases:
                break
        if not advanced:
            break

    return SemanticStressPanel(
        tuple(selected), tuple(selected_runs), generated,
        tuple(sorted(selected_dimensions.items())),
        coverage_report(program, selected_runs),
        tuple(sorted(rejected_statuses.items())),
        attempted_cases=attempted, executed_steps=executed,
        work_limits={'max_total_steps':max_total_steps, 'max_trials':max_trials,
                     'max_generated':max_generated},
        stop_reasons=tuple(stops), examined_mutations=examined,
        unattempted_seed_count=len(seed_cases)-len(seed_runs))


def explore_coverage(target_assembly: str,
                     seed_cases: tuple[TestCase, ...], *,
                     target_name: str = "target",
                     call_arities: dict[str, int] | None = None,
                     return_registers: tuple[str, ...] = (),
                     mutable_entry_registers: tuple[str, ...] =
                     ("a1", "a2", "a3"),
                     pointer_entry_registers: tuple[str, ...] = (),
                     max_cases: int = 5_000,
                     max_steps: int = 10_000, callee_environment=None,
                     max_total_steps: int | None = None) -> CoverageExploration:
    """Greedily retain inputs that cover a new target instruction or edge.

    This is deterministic boundary-guided exploration, not a feasibility
    proof.  Any unresolved outcome remains visible in the receipt.
    """
    if not seed_cases:
        raise ValueError("coverage exploration requires at least one seed case")
    if max_total_steps is not None and (type(max_total_steps) is not int or max_total_steps <= 0):
        raise ValueError('positive total instruction budget required')
    program = Program.parse(target_name, target_assembly)
    selected_cases: list[TestCase] = []
    selected_runs: list[RunResult] = []
    # Each retained path parent has two bounded phases.  Directed predicate
    # inversions run first; its broad boundary mutations run exactly once
    # afterward.  A deque lets newly discovered predicate paths preempt broad
    # enumeration without requeueing (and duplicating) an entire BFS level.
    frontier: deque[tuple[TestCase, RunResult, str]] = deque()
    discovery_instructions: set[int] = set()
    discovery_edges: set[tuple[int, bool]] = set()
    discovery_paths: set[tuple[tuple[int, bool], ...]] = set()
    seen: set[tuple] = set()
    attempted = 0
    executed_steps = 0
    obstructions = {}
    trial_counts = {}
    trial_failures = {}

    def record_obstruction(case, run):
        nonlocal executed_steps
        executed_steps += run.instruction_count
        trial_counts[run.status] = trial_counts.get(run.status,0)+1
        if run.status not in COMPLETED_STATUSES and (run.status,run.error) not in trial_failures and len(trial_failures)<8:
            trial_failures[(run.status,run.error)] = {
                'case':asdict(case),'status':run.status,'error':run.error,
                'instruction_count':run.instruction_count,
                'nested_calls':[{'callee':call['callee'],'status':call['execution']['status'],
                    'error':call['execution']['error'],'input_read_locations':call.get('input_read_locations',[])}
                    for call in run.concrete_calls[-2:]]}
        if run.status == 'unsupported' and run.error not in obstructions and len(obstructions) < 8:
            obstructions[run.error] = {'case':asdict(case), 'status':run.status, 'error':run.error}

    def key(case: TestCase) -> tuple:
        return (case.seed, case.player_writes, case.global_writes,
                case.entry_registers, case.call_returns)

    def add_discovery(run: RunResult) -> bool:
        """Retain novel prefixes even when execution ends in a harness fault.

        A newly entered path can expose the next controllable input before a
        random, invalid downstream value faults.  Those prefixes are useful
        search parents but do not enter the completed-run coverage report or
        semantic panel.
        """
        instructions = {event.instruction for event in run.trace}
        edges: set[tuple[int, bool]] = set()
        path: list[tuple[int, bool]] = []
        for event in run.trace:
            if event.effect.startswith("branch taken;"):
                edges.add((event.instruction, True))
                path.append((event.instruction, True))
            elif event.effect.startswith("branch not taken;"):
                edges.add((event.instruction, False))
                path.append((event.instruction, False))
        # Loop trip counts should not manufacture an unbounded number of path
        # identities.  Coverage needs the ordered set of observed outcomes;
        # repeating the same loop edge 200 times adds no new conjunction.
        signature = tuple(dict.fromkeys(path))
        novel = bool(instructions - discovery_instructions or
                     edges - discovery_edges or
                     signature not in discovery_paths)
        discovery_instructions.update(instructions)
        discovery_edges.update(edges)
        discovery_paths.add(signature)
        return novel

    for case in seed_cases:
        if key(case) in seen or attempted >= max_cases or (max_total_steps is not None and executed_steps >= max_total_steps):
            continue
        seen.add(key(case))
        run = execute_case(
            program, case, call_arities=call_arities,
            return_registers=return_registers, max_steps=min(max_steps,max_total_steps-executed_steps) if max_total_steps is not None else max_steps, callee_environment=callee_environment)
        record_obstruction(case, run)
        attempted += 1
        selected_cases.append(case)
        selected_runs.append(run)
        frontier.append((case, run, "directed"))
        add_discovery(run)

    report = coverage_report(program, selected_runs)
    while frontier and not report.complete and attempted < max_cases and (max_total_steps is None or executed_steps < max_total_steps):
        base_case, base_run, phase = frontier.popleft()
        broad_task: tuple[TestCase, RunResult, str] | None = None
        if phase == "directed":
            # Invert every observed path predicate as well as every globally
            # unresolved edge.  A deep edge can require a conjunction of
            # already-covered outcomes.  Schedule this parent's broad phase,
            # but let any new directed path run ahead of it.
            directed_edges = list(report.unresolved_branch_edges)
            for event in base_run.trace:
                if event.effect.startswith("branch taken;"):
                    directed_edges.append((event.instruction, False))
                elif event.effect.startswith("branch not taken;"):
                    directed_edges.append((event.instruction, True))
            candidates = _predicate_mutations(
                program, base_case, base_run,
                tuple(dict.fromkeys(directed_edges)),
                mutable_entry_registers=mutable_entry_registers)
            broad_task = (base_case, base_run, "broad")
            if base_run.status not in {"memory_fault", "step_limit"}:
                frontier.append(broad_task)
        else:
            candidates = _mutations(
                program, base_case, base_run,
                mutable_entry_registers=mutable_entry_registers,
                pointer_entry_registers=pointer_entry_registers)

        discovered: list[tuple[TestCase, RunResult, str]] = []
        resume_broad: tuple[TestCase, RunResult, str] | None = None
        for candidate in candidates:
            if attempted >= max_cases or (max_total_steps is not None and executed_steps >= max_total_steps):
                break
            identity = key(candidate)
            if identity in seen:
                continue
            seen.add(identity)
            run = execute_case(
                program, candidate, call_arities=call_arities,
                return_registers=return_registers,
                max_steps=min(max_steps,max_total_steps-executed_steps) if max_total_steps is not None else max_steps, callee_environment=callee_environment)
            record_obstruction(candidate, run)
            attempted += 1
            discovery_novel = add_discovery(run)
            trial = coverage_report(program, selected_runs + [run])
            completed_improvement = (
                run.status in COMPLETED_STATUSES and (
                    len(trial.covered_instructions) >
                    len(report.covered_instructions) or
                    len(trial.covered_branch_edges) >
                    len(report.covered_branch_edges)))
            if completed_improvement:
                selected_cases.append(candidate)
                selected_runs.append(run)
                report = trial
            if completed_improvement or discovery_novel:
                discovered.append((candidate, run, "directed"))
            if (phase == "broad" and discovery_novel and
                    run.status in {"memory_fault", "step_limit"}):
                # Follow a newly deeper failing prefix immediately.  The
                # remaining mutations of this parent are still useful, but
                # exhausting hundreds of its siblings before repairing the
                # next exposed input destroys the gradient on large bodies.
                # Requeueing is safe because `seen` skips candidates already
                # tried when this broad phase resumes.
                resume_broad = (base_case, base_run, "broad")
                break
            if report.complete:
                break
        frontier.extendleft(reversed(discovered))
        if resume_broad is not None:
            frontier.append(resume_broad)
        if (broad_task is not None and
                base_run.status in {"memory_fault", "step_limit"}):
            # A faulting or non-terminating prefix has already exposed the
            # next environmental reads.  Repair those values before exploring
            # more branch combinations.  In renderers an output pointer can
            # alias the input object: writes then corrupt a later loop bound,
            # which appears as a step limit rather than a memory fault.
            frontier.appendleft(broad_task)
    if report.complete:
        stop_reason = "all reachable instructions and conditional edges covered"
    elif max_total_steps is not None and executed_steps >= max_total_steps:
        stop_reason = 'total instruction budget exhausted with unresolved coverage targets'
    elif attempted >= max_cases:
        stop_reason = "case budget exhausted with unresolved coverage targets"
    else:
        stop_reason = "boundary mutation frontier exhausted"
    return CoverageExploration(
        tuple(selected_cases), tuple(selected_runs), report,
        attempted, stop_reason, tuple(obstructions.values()),dict(trial_counts),tuple(trial_failures.values()),
        executed_steps,max_total_steps)
