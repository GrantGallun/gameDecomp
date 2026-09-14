"""Replay a compiled candidate against a RECORDED call of the original function.

The semantic panel tests candidates against memory it invents: synthetic inputs,
guessed object extents, synthetic globals and opaque callees. Across the 1,312
unmatched functions (2026-09-13) only 358 passed cleanly; the rest passed with
execution debt, failed, or could not run. A recorded call removes those guesses
for the paths the game actually took:

    initial memory   every byte the call read before writing it, as recorded
    entry registers  as recorded at the function's first instruction
    callees          recorded return value, and recorded memory writes applied
                     at the same call ordinal -- nothing is executed or guessed
    devices          recorded read values

THE ORIGINAL IS REPLAYED FIRST, and a recording is only usable if that replay
reproduces the recorded call exactly: the same body writes in order, the same
callee arguments, the same return. An interrupt mid-call, a device value that
changed between reads, or an unmodelled instruction all fail that gate, and the
recording is then reported unusable rather than used to judge a candidate.

Recordings are EVIDENCE: they come from executing the original ROM. They cover
the paths the session exercised, not every input, so a pass is behavioural
evidence for those calls, never a proof and never an exactness verdict.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import struct

from solver import mips_differential as d

# Project64 4.x type IDs. The API names them without numbers; this order was
# verified against real instructions (lhu->1, lh->4, lw->5, sh->4) and newer
# recordings carry the live values in `type_ids`, which take precedence.
VERIFIED_TYPE_IDS = {"u8": 0, "u16": 1, "u32": 2, "s8": 3, "s16": 4, "s32": 5,
                     "f32": 6, "f64": 7, "u64": 8}
WIDTH = {"u8": 1, "s8": 1, "u16": 2, "s16": 2, "u32": 4, "s32": 4, "f32": 4, "f64": 8, "u64": 8}
RAM = d.Region("recorded-ram", 0x80000000, 0x00800000, "persistent")
DEVICE = d.Region("recorded-device", 0xA0000000, 0x20000000, "persistent")
REGISTER_ORDER = ("zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 "
                  "s0 s1 s2 s3 s4 s5 s6 s7 t8 t9 k0 k1 gp sp fp ra").split()


class UnusableRecording(ValueError):
    """The recording cannot judge a candidate; the reason says why."""


def _encode(type_name: str, value, value_hi) -> bytes:
    if value is None:
        # Project64 reported an access without its value (seen on float accesses in
        # __MusIntMain, 2026-09-13). Starting memory would be guessed: refuse.
        raise UnusableRecording(f"{type_name} access recorded without a value")
    if type_name == "f32":
        return struct.pack(">f", float(value))
    if type_name == "f64":
        return struct.pack(">d", float(value))
    if type_name == "u64":
        return struct.pack(">II", int(value_hi or 0) & 0xFFFFFFFF, int(value) & 0xFFFFFFFF)
    width = WIDTH[type_name]
    return (int(value) & ((1 << (8 * width)) - 1)).to_bytes(width, "big")


@dataclass
class RecordedCall:
    ordinal: int
    site: int
    target: int
    arguments: tuple[int, ...] | None = None
    v0: int | None = None
    v1: int | None = None
    writes: list[tuple[int, bytes]] = field(default_factory=list)


@dataclass
class Decoded:
    initial: dict[int, int]
    body_writes: list[tuple[int, int, bytes]]
    calls: list[RecordedCall]
    foreign_accesses: int
    device_accesses: int
    entry_registers: dict[str, int]
    exit_registers: dict[str, int]


def decode(record: dict, entry: int, end: int) -> Decoded:
    """Attribute every recorded access and rebuild the call's starting memory."""
    names = {v: k for k, v in (record.get("type_ids") or VERIFIED_TYPE_IDS).items()}
    initial: dict[int, int] = {}
    written: set[int] = set()
    body_writes, calls = [], []
    current: RecordedCall | None = None
    foreign = device = 0
    for event in record["events"]:
        kind = event[0]
        if kind == "c":
            current = RecordedCall(ordinal=len(calls), site=event[1], target=event[2])
            calls.append(current)
            continue
        if kind == "e":
            if current is not None and current.arguments is None:
                current.arguments = tuple(int(x) & 0xFFFFFFFF for x in event[2:6])
            continue
        if kind == "x":
            if current is not None:
                current.v0, current.v1 = int(event[2]) & 0xFFFFFFFF, int(event[3]) & 0xFFFFFFFF
            current = None
            continue
        pc, address, type_id, value, value_hi = event[1], event[2], event[3], event[4], event[5]
        type_name = names.get(type_id)
        if type_name is None:
            raise UnusableRecording(f"unknown access type id {type_id}")
        raw = _encode(type_name, value, value_hi)
        in_body = entry <= pc < end
        if kind in "RW":
            device += 1
        if kind in "rR":
            for offset, byte in enumerate(raw):
                where = address + offset
                if where not in written and where not in initial:
                    initial[where] = byte
            continue
        written.update(range(address, address + len(raw)))
        if in_body:
            body_writes.append((pc, address, raw))
        elif current is not None:
            current.writes.append((address, raw))
        else:
            foreign += 1              # an interrupt or another thread mid-call
    gpr = record["entry"]["gpr"]
    exit_gpr = (record.get("exit") or {}).get("gpr") or []
    return Decoded(initial=initial, body_writes=body_writes, calls=calls,
                   foreign_accesses=foreign, device_accesses=device,
                   entry_registers={n: gpr[i] & 0xFFFFFFFF for i, n in enumerate(REGISTER_ORDER) if n != "zero"},
                   exit_registers={n: exit_gpr[i] & 0xFFFFFFFF for i, n in enumerate(REGISTER_ORDER)
                                   if exit_gpr and n != "zero"})


class RecordedEffects:
    """`callee_environment.outputs` hook: apply one callee's recorded writes."""

    def __init__(self, by_ordinal: dict[int, RecordedCall]):
        self.by_ordinal = by_ordinal

    def labels(self, runner, raw, labels):
        return labels

    def apply(self, runner, raw, arguments, ordinal, returned, instruction):
        call = self.by_ordinal.get(ordinal)
        if call is None:
            return
        for address, payload in call.writes:
            for offset, byte in enumerate(payload):
                runner.memory.write(address + offset, 1, byte)


class _Environment:
    def __init__(self, outputs):
        self.leaves, self.outputs, self.callbacks = {}, outputs, ()

    def manifest(self):
        return {"kind": "recorded-callee-effects", "callees": sorted(self.outputs)}


# The function's own frame is scratch, not behaviour: a candidate with a
# different frame size saves `ra` at a different address and is otherwise
# identical. Everything below the entry stack pointer, plus the four o32
# argument home slots the callee may write, is a "stack" region -- which the
# interpreter excludes from compared memory and from recorded write events.
# The caller's frame above that stays compared.
STACK_WINDOW = 0x10000
HOME_SLOTS = 0x10


def stack_region(decoded: "Decoded") -> d.Region:
    sp = decoded.entry_registers["sp"]
    return d.Region("recorded-stack", sp - STACK_WINDOW, STACK_WINDOW + HOME_SLOTS, "stack")


HEX_NAMED = __import__("re").compile(r"(?:D|jtbl|func)_[0-9A-Fa-f]{6,8}")


def program_symbols(program, real: dict[str, int], callees: set[str]) -> d.SymbolTable:
    """One symbol table PER program.

    Real linker addresses are shared, so recorded memory lines up. A program's
    own embedded data -- a candidate's `.rodata` jump table, float constants,
    strings -- has no real address and differs between the original and the
    candidate, so it gets that program's own synthetic address and region.
    Only a true global with neither a real address nor embedded bytes is
    unbound, and that makes the recording unusable for this program.
    """
    names = set(program.symbols)
    local = {n for n, _ in program.data_words} | {n for n, _ in program.data_bytes}
    addresses = {n: real[n] for n in names if n in real}
    addresses.update(program.symbol_addresses)
    unbound = sorted(n for n in names - addresses.keys() - local if not HEX_NAMED.fullmatch(n))
    if unbound:
        raise UnusableRecording("symbols without linker addresses: " + ", ".join(unbound[:8]))
    return d.SymbolTable(names | callees, addresses)


def embedded_data(program, symbols: d.SymbolTable) -> tuple[list[d.Region], dict[int, int]]:
    """Regions and bytes for a program's own initialized data, as the harness seeds it."""
    extents, values = {}, {}
    for (name, offset), byte in program.data_bytes.items():
        extents[name] = max(extents.get(name, 0), offset + 1)
        values[symbols.address(name) + offset] = byte
    for (name, offset), text_offset in program.data_words.items():
        extents[name] = max(extents.get(name, 0), offset + 4)
        for i, byte in enumerate(((program.text_base + text_offset) & 0xFFFFFFFF).to_bytes(4, "big")):
            values[symbols.address(name) + offset + i] = byte
    regions = [d.Region("&" + name, symbols.address(name), size) for name, size in sorted(extents.items())
               if not (RAM.contains(symbols.address(name), size) or DEVICE.contains(symbols.address(name), size))]
    return regions, values


def _run(program, decoded, symbols, callee_names, arities, return_registers, max_steps):
    local_regions, local_bytes = embedded_data(program, symbols)
    memory = d.Memory([stack_region(decoded), *local_regions, RAM, DEVICE],
                      {**decoded.initial, **local_bytes})
    memory.mark_clean()
    by_ordinal = {c.ordinal: c for c in decoded.calls}
    effects = RecordedEffects(by_ordinal)
    outputs = {name: effects for name in set(callee_names.values())}
    call_returns = {(callee_names[c.target], c.ordinal): c.v0 for c in decoded.calls if c.v0 is not None}
    runner = d.Runner(program, memory, dict(decoded.entry_registers), symbols,
                      call_arities=arities, max_steps=max_steps, return_registers=return_registers,
                      call_returns=call_returns, callee_environment=_Environment(outputs))
    return runner.execute()


def _gate(decoded: Decoded, run: d.RunResult, callee_names: dict[int, str], arities: dict[str, int],
          return_registers) -> list[str]:
    """Why the ORIGINAL's replay does not reproduce its own recording, if it doesn't."""
    # Interrupt/thread accesses mid-call are NOT an automatic rejection: one that
    # never touches memory this function uses changes nothing, and one that
    # does makes the original's replay diverge below, which rejects it anyway.
    problems = []
    if run.status not in d.COMPLETED_STATUSES:
        problems.append(f"original replay ended {run.status}: {run.error}")
        return problems
    replayed = [(w.raw_address, w.width, w.value) for w in run.writes]
    stack = stack_region(decoded)
    recorded = [(a, len(raw), int.from_bytes(raw, "big")) for _pc, a, raw in decoded.body_writes
                if not stack.contains(a, len(raw))]
    if replayed != recorded:
        first = next((i for i, (x, y) in enumerate(zip(replayed, recorded)) if x != y),
                     min(len(replayed), len(recorded)))
        problems.append(f"original replay writes differ from the recording at write #{first} "
                        f"({len(replayed)} replayed vs {len(recorded)} recorded)")
    if [c.callee for c in run.calls] != [callee_names[c.target] for c in decoded.calls]:
        problems.append("original replay call sequence differs from the recording")
    else:
        for event, call in zip(run.calls, decoded.calls):
            n = min(arities.get(event.callee, 0), 4)
            if call.arguments is not None and tuple(event.raw_arguments[:n]) != call.arguments[:n]:
                problems.append(f"original replay passes different arguments to {event.callee} call #{call.ordinal}")
                break
    for name in return_registers:
        if name in decoded.exit_registers and run.return_values.get(name) != decoded.exit_registers[name]:
            problems.append(f"original replay returns {name}={run.return_values.get(name, 0):#010x}, "
                            f"recorded {decoded.exit_registers[name]:#010x}")
    return problems


def replay(record: dict, target_assembly: str, candidate_assembly: str, *, entry: int, size: int,
           arities: dict[str, int], return_registers: tuple[str, ...], symbol_map: dict[str, int],
           max_steps: int = 50_000, fields: dict | None = None) -> dict:
    decoded = decode(record, entry, entry + size)
    target = replace(d.Program.parse("recorded_target", target_assembly), text_base=entry)
    candidate = d.Program.parse("recorded_candidate", candidate_assembly)
    real = {**symbol_map, **target.symbol_addresses}
    by_address = {}
    for name, address in real.items():
        by_address.setdefault(address, name)
    callee_names = {}
    for call in decoded.calls:
        if call.target not in by_address:
            raise UnusableRecording(f"recorded call target {call.target:#010x} has no symbol")
        callee_names[call.target] = by_address[call.target]
    arities = {**{name: 4 for name in callee_names.values()}, **arities}
    callees = set(callee_names.values())
    target_symbols = program_symbols(target, real, callees)
    original = _run(target, decoded, target_symbols, callee_names, arities, return_registers, max_steps)
    problems = _gate(decoded, original, callee_names, arities, return_registers)
    report = {"schema_version": 1, "function": record.get("function"), "entry_ordinal": record.get("entry_ordinal"),
              "recorded_calls": len(decoded.calls), "body_writes": len(decoded.body_writes),
              "initial_bytes": len(decoded.initial), "device_accesses": decoded.device_accesses,
              "interrupt_accesses": decoded.foreign_accesses, "authoritative": False,
              "scope": "one recorded call of the original ROM; paths not taken are untested"}
    if problems:
        return {**report, "status": "unusable", "reasons": problems}
    candidate_symbols = program_symbols(candidate, {**symbol_map, **candidate.symbol_addresses}, callees)
    candidate_run = _run(candidate, decoded, candidate_symbols, callee_names, arities, return_registers, max_steps)
    compared = d._compare_runs(d.TestCase("recorded-call-%s" % record.get("entry_ordinal"), 0),
                               original, candidate_run)
    return {**report, "status": compared.status, "reasons": list(compared.reasons),
            "first_divergence": compared.first_divergence,
            "feedback": d.repair_feedback(compared) if compared.status == "failed" else "",
            "distance": distance(compared) if compared.target and compared.candidate else None,
            "offset_constraints": offset_constraints(compared, decoded) if compared.status == "failed" else [],
            "divergence": describe(compared, decoded, fields)}


def distance(result: d.DifferentialResult) -> int:
    """How many recorded observables the candidate still gets wrong.

    Partial credit for ranking only: a failed recording is all-or-nothing, so a
    repair fixing one of two wrong arguments in every call scored identically
    to no repair, the search saw a stall, and restarted from the original. Units:
    one per differing write (address, width or value), per differing call
    argument, per wrong callee, per missing or extra write/call, per differing
    return register. Positional, so an inserted store also shifts later writes;
    that over-counts, it never hides a difference. Never an exactness signal.
    """
    target, candidate = result.target, result.candidate
    if candidate.status not in d.COMPLETED_STATUSES:
        # A candidate that faults or runs away has matched nothing observable.
        return 1 + len(target.writes) + sum(1 + len(c.raw_arguments) for c in target.calls) + len(target.return_values)
    total = abs(len(target.writes) - len(candidate.writes))
    total += sum((w.raw_address, w.width, w.value) != (g.raw_address, g.width, g.value)
                 for w, g in zip(target.writes, candidate.writes))
    for want, got in zip(target.calls, candidate.calls):
        if want.callee != got.callee:
            total += 1 + max(len(want.raw_arguments), len(got.raw_arguments))
            continue
        total += sum(x != y for x, y in zip(want.raw_arguments, got.raw_arguments))
        total += abs(len(want.raw_arguments) - len(got.raw_arguments))
    total += sum(1 + len(c.raw_arguments) for c in (target.calls[len(candidate.calls):]
                                                     or candidate.calls[len(target.calls):]))
    names = set(target.return_values) | set(candidate.return_values)
    total += sum(target.return_values.get(n) != candidate.return_values.get(n) for n in names)
    return total


def _base(address: int, registers: dict[str, int]):
    best = None
    for name in ("a0", "a1", "a2", "a3"):
        base = registers.get(name, 0)
        if 0x80000000 <= base <= address and address - base < 0x10000:
            if best is None or address - base < best[1]:
                best = (name, address - base)
    return best


def offset_constraints(result: d.DifferentialResult, decoded: Decoded) -> list[dict]:
    """Observables that differ ONLY in which offset of an entry pointer they use.

    A call argument the original passes as a0+0x38 and the candidate as a0+0x28,
    or a same-width same-value store at a0+0x38 versus a0+0x28, says: the
    candidate's expression for that observable should reach offset 0x38. Only
    unambiguous alignments count -- same callee sequence for arguments, same
    write count for stores -- and both addresses must resolve to the same entry
    register. These are constraints for a repair to test, not conclusions.
    """
    target, candidate = result.target, result.candidate
    regs, rows = decoded.entry_registers, []

    def add(kind, where, want, got):
        a, b = _base(want, regs), _base(got, regs)
        if a and b and a[0] == b[0] and a[1] != b[1]:
            rows.append({"kind": kind, "where": where, "register": a[0],
                         "candidate_offset": b[1], "target_offset": a[1]})

    if [c.callee for c in target.calls] == [c.callee for c in candidate.calls]:
        for want, got in zip(target.calls, candidate.calls):
            for index, (x, y) in enumerate(zip(want.raw_arguments, got.raw_arguments)):
                if x != y:
                    add("argument", f"call #{want.ordinal} {want.callee} argument {index + 1}", x, y)
    if len(target.writes) == len(candidate.writes):
        for index, (want, got) in enumerate(zip(target.writes, candidate.writes)):
            if want.raw_address != got.raw_address and (want.width, want.value) == (got.width, got.value):
                add("write", f"write #{index}", want.raw_address, got.raw_address)
    return rows


def member_at(parameter: str, rows: list[dict], offset: int) -> str | None:
    """C spelling for `offset` bytes into `*parameter`, from compiler-measured rows.

    Rows are `type_constraints.measure` layouts: member path, offset, width. The
    innermost member containing the offset wins; an offset inside an array or
    padding is spelled as a byte distance from that member, never as a guessed
    element. Names are the CANDIDATE's declarations, not facts about the original.
    """
    best = None
    for row in rows:
        start, width = row.get("offset"), row.get("width")
        if start is None or not width or not start <= offset < start + width:
            continue
        if best is None or (width, -row["member"].count(".")) < (best["width"], -best["member"].count(".")):
            best = row
    if best is None:
        return None
    if offset == best["offset"] and not best.get("array"):
        return f"&{parameter}->{best['member']}"
    return f"&{parameter}->{best['member']} + {offset - best['offset']:#x} bytes"


def _relative(address: int, registers: dict[str, int], fields: dict | None = None) -> str:
    """Name an address by the nearest entry pointer register at or below it."""
    best = None
    for name in ("a0", "a1", "a2", "a3", "sp", "gp"):
        base = registers.get(name, 0)
        if 0x80000000 <= base <= address and address - base < 0x10000:
            if best is None or address - base < best[1]:
                best = (name, address - base)
    if not best:
        return f"{address:#010x}"
    text = f"{best[0]}+{best[1]:#x}"
    parameter = (fields or {}).get(best[0])
    member = member_at(parameter["parameter"], parameter["rows"], best[1]) if parameter else None
    return f"{text} ({member})" if member else text


def describe(result: d.DifferentialResult, decoded: Decoded, fields: dict | None = None) -> dict:
    """The first observable difference, in terms a C repair can act on.

    `fields` maps entry registers to the candidate's parameter name and measured
    record layout, so `a0+0x38` also reads `&arg0->image2`.
    """
    target, candidate = result.target, result.candidate
    regs = decoded.entry_registers

    for index, (want, got) in enumerate(zip(target.writes, candidate.writes)):
        if (want.raw_address, want.width, want.value) != (got.raw_address, got.width, got.value):
            return {"kind": "write", "index": index,
                    "target": {"at": _relative(want.raw_address, regs, fields), "width": want.width,
                               "value": f"{want.value:#x}", "instruction": want.instruction},
                    "candidate": {"at": _relative(got.raw_address, regs, fields), "width": got.width,
                                  "value": f"{got.value:#x}", "instruction": got.instruction}}
    if len(target.writes) != len(candidate.writes):
        return {"kind": "write_count", "target": len(target.writes), "candidate": len(candidate.writes)}
    def argument(value):
        # A pointer argument means nothing to a C edit as 0x8011df78; as
        # a0+0x30 it names the field of the function's own input.
        return _relative(value, regs, fields) if 0x80000000 <= value < 0x80800000 else f"{value:#x}"

    def call_row(want, got):
        return {"ordinal": want.ordinal,
                "differing_arguments": [i for i, (x, y) in enumerate(zip(want.raw_arguments, got.raw_arguments))
                                        if x != y],
                "target": {"callee": want.callee, "arguments": [argument(x) for x in want.raw_arguments]},
                "candidate": {"callee": got.callee, "arguments": [argument(x) for x in got.raw_arguments]}}

    pairs = list(zip(target.calls, candidate.calls))
    for index, (want, got) in enumerate(pairs):
        if (want.callee, want.raw_arguments) != (got.callee, got.raw_arguments):
            # Every later differing call too: one call shows a wrong offset, the
            # sequence shows the layout (image_n at 0x38+4n, not 0x28+8n).
            later = [call_row(w, g) for w, g in pairs[index + 1:]
                     if (w.callee, w.raw_arguments) != (g.callee, g.raw_arguments)]
            return {"kind": "call", **call_row(want, got), "later_differing_calls": later[:7],
                    "omitted_differing_calls": max(0, len(later) - 7)}
    if len(target.calls) != len(candidate.calls):
        return {"kind": "call_count", "target": len(target.calls), "candidate": len(candidate.calls)}
    if target.return_values != candidate.return_values:
        return {"kind": "return", "target": {k: f"{v:#x}" for k, v in target.return_values.items()},
                "candidate": {k: f"{v:#x}" for k, v in candidate.return_values.items()}}
    return {"kind": "none"}


def sentence(divergence: dict | None) -> str:
    """One plain sentence a C repair can act on."""
    if not divergence or divergence.get("kind") == "none":
        return "no observable difference"
    kind = divergence["kind"]
    if kind == "write":
        want, got = divergence["target"], divergence["candidate"]
        if want["at"] != got["at"] and want["value"] == got["value"] and want["width"] == got["width"]:
            return (f"the original writes {want['value']} ({8 * want['width']}-bit) to {want['at']}; "
                    f"the candidate writes the same value to {got['at']} -- a wrong field offset or index")
        if want["at"] == got["at"] and want["width"] != got["width"]:
            return (f"at {want['at']} the original writes {8 * want['width']} bits and the candidate "
                    f"{8 * got['width']} -- a wrong field width or type")
        if want["at"] == got["at"]:
            return (f"at {want['at']} the original writes {want['value']} and the candidate writes "
                    f"{got['value']} -- a wrong computed value")
        return (f"write #{divergence['index']}: the original writes {want['value']} to {want['at']}, "
                f"the candidate writes {got['value']} to {got['at']}")
    if kind == "write_count":
        return (f"the original performs {divergence['target']} memory writes, the candidate "
                f"{divergence['candidate']} -- a missing or extra store")
    if kind == "call":
        def one(row):
            want, got = row["target"], row["candidate"]
            detail = "; ".join(f"argument {i + 1} is {want['arguments'][i]} in the original but "
                               f"{got['arguments'][i]} in the candidate"
                               for i in row.get("differing_arguments", []))
            return (f"call #{row['ordinal']} to {want['callee']}: {detail or 'arguments differ'} "
                    f"(original {want['callee']}({', '.join(want['arguments'])}), "
                    f"candidate {got['callee']}({', '.join(got['arguments'])}))")
        text = one(divergence)
        later = divergence.get("later_differing_calls") or []
        if later:
            text += "; also " + "; ".join(one(row) for row in later)
            if divergence.get("omitted_differing_calls"):
                text += f"; {divergence['omitted_differing_calls']} more differing calls omitted"
        return text
    if kind == "call_count":
        return f"the original makes {divergence['target']} calls, the candidate {divergence['candidate']}"
    if kind == "return":
        return f"the original returns {divergence['target']}, the candidate returns {divergence['candidate']}"
    return str(divergence)


def feedback_item(report: dict) -> dict:
    """A semantic-panel counterexample built from a real recorded call.

    The standing semantic prompt warns the model not to trust synthetic callee
    results. These are not synthetic, and saying so is the point.
    """
    return {"source": "recorded game execution of the original ROM",
            "recording": report.get("recording"), "entry_ordinal": report.get("entry_ordinal"),
            "reasons": report.get("reasons", []),
            "difference": sentence(report.get("divergence")),
            "divergence": report.get("divergence"),
            "first_divergence": str(report.get("first_divergence", ""))[:600],
            "evidence": "initial memory, callee return values and callee writes were recorded from the "
                        "running game, not synthesized; the stack frame is excluded from comparison",
            "reading": "a0+0x38 means 0x38 bytes past the function's first parameter at entry. When the C "
                       "names a struct field there, the field it named sits at the candidate offset; the "
                       "original used whatever is declared at the original offset, which may be a "
                       "differently named field of the included type definitions"}


def prompt(reports: list[dict], limit: int = 3) -> str:
    """Compact, evidence-labelled text for a repair prompt."""
    failed = [r for r in reports if r.get("status") == "failed"][:limit]
    usable = [r for r in reports if r.get("status") in ("passed", "failed")]
    if not usable:
        return ""
    lines = [f"\nRECORDED GAME EXECUTION (evidence: {len(usable)} real calls captured from the original ROM "
             f"running the game; memory, callee returns and callee writes are recorded, not synthetic; "
             f"the stack frame is excluded):"]
    passed = sum(r["status"] == "passed" for r in usable)
    lines.append(f"- {passed} of {len(usable)} recorded calls behave identically under the current candidate.")
    if passed == len(usable):
        lines.append("- Behaviour already matches on these calls: repair code SHAPE (registers, ordering, "
                     "frame, types) without changing what the function computes.")
    for r in failed:
        lines.append(f"- Recorded call #{r.get('entry_ordinal')}: {sentence(r.get('divergence'))}.")
    return "\n".join(lines)
