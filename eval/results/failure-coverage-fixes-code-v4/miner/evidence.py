"""Extract the evidence tier from a decomp target.

Evidence is what the binary *says*, never what we think it means. A `lbu` at
offset 0x24 gives width 1, unsigned, int class -- observation, not
interpretation. Nothing in here may guess.

The one judgement call is resolving a memory access's base register to
something symbolic (param0, stack, a global address). That resolution is
deliberately conservative: it claims a base only when it can prove one, and
records 'unknown' otherwise. Invariant 5 -- unknown must be representable, and
is always preferable to a plausible guess.

Usage:
    python -m miner.evidence --repo ~/decomp/sbk1 --target sbk1 --db kb.sqlite
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import rabbitizer

OBJDUMP = "mips-linux-gnu-objdump"

# Derived empirically via tools/probe_rabbitizer.py -- do not assume, re-probe
# if the rabbitizer version changes.
ACCESS_WIDTH = {
    "BYTE": 1,
    "SHORT": 2,
    "WORD": 4,
    "DOUBLEWORD": 8,
    "FLOAT": 4,
    "DOUBLEFLOAT": 8,
    "WORD_LEFT": 4,
    "WORD_RIGHT": 4,
    "DOUBLEWORD_LEFT": 8,
    "DOUBLEWORD_RIGHT": 8,
    "QUADWORD": 16,
}
PARTIAL_ACCESS = {"WORD_LEFT", "WORD_RIGHT", "DOUBLEWORD_LEFT", "DOUBLEWORD_RIGHT"}

# Clobbered across a call under the O32 ABI. After a jal we can no longer claim
# anything about these, and saying otherwise would fabricate evidence.
CALLER_SAVED = {
    "v0", "v1", "a0", "a1", "a2", "a3",
    "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7", "t8", "t9",
    "at", "ra",
}

# Survive a branch merge: only registers whose value is structurally fixed.
BRANCH_STABLE = {"sp", "gp"}

FUNC_HEADER_RE = re.compile(r"^([0-9a-f]+) <([^>]+)>:$")
INSN_RE = re.compile(r"^\s*([0-9a-f]+):\s+([0-9a-f]{8})\s+(.*)$")


# --------------------------------------------------------------------- model


@dataclass
class Insn:
    addr: int
    word: int
    decoded: rabbitizer.Instruction


@dataclass
class Func:
    addr: int
    name: str
    insns: list[Insn]

    @property
    def size(self) -> int:
        return len(self.insns) * 4

    @property
    def is_leaf(self) -> bool:
        return not any(i.decoded.isFunctionCall() for i in self.insns)


# ----------------------------------------------------------------- build gate


def verify_build(repo: Path) -> str:
    """Refuse to extract from a build that is not green.

    Invariant 1: the tree is only ever asm or verified-matching. Evidence taken
    from a broken build would be evidence about code the console never ran.
    Returns the verified SHA1.
    """
    sha_files = list(repo.glob("*.sha1"))
    if not sha_files:
        raise SystemExit(f"no .sha1 file in {repo}")
    expected_line = sha_files[0].read_text().split()
    expected, rel = expected_line[0], expected_line[1]

    built = repo / rel
    if not built.exists():
        raise SystemExit(f"{built} not built -- run tools/build-and-verify.sh first")

    actual = hashlib.sha1(built.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(
            f"BUILD IS NOT GREEN\n  expected {expected}\n  actual   {actual}\n"
            "Refusing to extract evidence from a non-matching build."
        )
    return actual


# ---------------------------------------------------------------- disassembly


def function_symbols(elf: Path) -> set[str]:
    """Names of symbols the ELF marks as functions (STT_FUNC).

    Data objects living inside a disassembled section get labels too, and
    objdump will happily decode their bytes as instructions. Those decodes are
    not observations of code -- they are noise shaped like evidence, which is
    the one thing the evidence tier must never contain. Jump tables and packed
    tables are the common offenders.

    objdump -t marks functions with a standalone 'F' token and data with 'O'.
    """
    proc = subprocess.run(
        [OBJDUMP, "-t", str(elf)], capture_output=True, text=True, check=True)

    names = set()
    for line in proc.stdout.splitlines():
        tokens = line.split()
        if len(tokens) >= 4 and "F" in tokens[1:-1]:
            names.add(tokens[-1])
    return names


def function_ranges(elf: Path) -> dict[tuple[int, str], int]:
    """ELF extents by address AND name; zero-size aliases need a unique peer."""
    proc = subprocess.run([OBJDUMP,'-t',str(elf)],capture_output=True,text=True,check=True)
    ranges = {}
    for line in proc.stdout.splitlines():
        tokens = line.split()
        if len(tokens)<6 or 'F' not in tokens[1:-1] or not tokens[-3].startswith('.'):
            continue
        address,size = int(tokens[0],16),int(tokens[-2],16)
        key = (address,tokens[-1])
        if key in ranges and ranges[key] != size:
            raise ValueError('conflicting ELF function extents: '+repr(key))
        ranges[key] = size
    for key,size in list(ranges.items()):
        if not size:
            peers = {n for (address,_),n in ranges.items() if address==key[0] and n>0}
            if len(peers)==1:
                ranges[key] = next(iter(peers))
    return ranges


def disassemble(elf: Path, functions_only: bool = True) -> list[Func]:
    """Run objdump and decode every instruction with rabbitizer.

    ELF STT_FUNC address/size records bound the raw words; printed disassembly
    labels do not define extents. Internal labels and unlabeled following code
    must neither truncate nor extend functions. Rabbitizer does the semantic
    decoding; operand text is not used as evidence. -z retains zero words.

    `functions_only` filters to STT_FUNC symbols. Leave it on for anything that
    produces evidence.
    """
    ranges = function_ranges(elf) if functions_only else None

    proc = subprocess.run(
        [OBJDUMP, "-d", "-z", "--show-raw-insn", str(elf)],
        capture_output=True, text=True, check=True,
    )

    funcs: list[Func] = []
    current: Func | None = None
    current_end = None
    raw_words = {}

    for line in proc.stdout.splitlines():
        header = FUNC_HEADER_RE.match(line)
        if header:
            name = header.group(2)
            address = int(header.group(1),16)
            if ranges is not None and (address,name) not in ranges:
                current = None          # data object: decode nothing from it
                continue
            size = ranges.get((address,name)) if ranges is not None else None
            if size is not None and (size<=0 or size%4 or address%4):
                raise ValueError(f'unknown or unaligned ELF function range: {name} at {address:#x}, size={size}')
            current_end = address+size if size is not None else None
            current = Func(addr=address, name=name, insns=[])
            funcs.append(current)
            continue

        if ranges is not None:
            insn = INSN_RE.match(line)
            if insn:
                address,word = int(insn.group(1),16),int(insn.group(2),16)
                if address in raw_words and raw_words[address] != word:
                    raise ValueError(f'ambiguous disassembly word at {address:#x}')
                raw_words[address] = word
            continue
        if current is None:
            continue

        insn = INSN_RE.match(line)
        if not insn:
            continue

        addr = int(insn.group(1), 16)
        if current_end is not None and addr >= current_end:
            current = None
            continue
        if addr != current.addr+len(current.insns)*4:
            raise ValueError(f'noncontiguous function disassembly: {current.name} at {addr:#x}')
        word = int(insn.group(2), 16)
        current.insns.append(
            Insn(addr=addr, word=word,
                 decoded=rabbitizer.Instruction(word, vram=addr))
        )

    if ranges is not None:
        for func in funcs:
            size = ranges[(func.addr,func.name)]
            addresses = range(func.addr,func.addr+size,4)
            missing = [address for address in addresses if address not in raw_words]
            if missing:
                raise ValueError(f'incomplete ELF function disassembly: {func.name}, missing word at {missing[0]:#x}')
            func.insns = [Insn(addr=address,word=raw_words[address],
                          decoded=rabbitizer.Instruction(raw_words[address],vram=address))
                          for address in addresses]
    return [f for f in funcs if f.insns]


# ----------------------------------------------------------- base resolution

def verify_rom_words(funcs, rom: bytes, config: dict) -> list[dict]:
    """Bind every decoded word to the identity-checked, mapped target ROM."""
    from solver.sdk_intake import rom_offset
    if hashlib.sha1(rom).hexdigest() != config['sha1']:
        raise ValueError('ROM identity differs from extraction configuration')
    bindings = []
    for func in funcs:
        offset = rom_offset(config,func.addr,func.size,len(rom))
        if [i.addr for i in func.insns] != list(range(func.addr,func.addr+func.size,4)):
            raise ValueError('noncontiguous function words: '+func.name)
        words = b''.join(i.word.to_bytes(4,'big') for i in func.insns)
        if words != rom[offset:offset+func.size]:
            raise ValueError('ELF function words differ from ROM: '+func.name)
        bindings.append({'function':func.name,'address':func.addr,'bytes':func.size,
                         'rom_offset':offset,'slice_sha256':hashlib.sha256(words).hexdigest()})
    return bindings


def bind_repo_rom(repo: Path, funcs) -> list[dict]:
    import yaml
    config = yaml.safe_load((repo/'snowboardkids.yaml').read_text())
    rom = (repo/config['options']['target_path']).read_bytes()
    return verify_rom_words(funcs,rom,config)


def branch_targets(func: Func) -> set[int]:
    """Addresses reachable by a branch, where register state must be dropped."""
    targets = set()
    for insn in func.insns:
        d = insn.decoded
        if d.isBranch() and not d.isFunctionCall():
            try:
                targets.add(d.getBranchVramGeneric())
            except (RuntimeError, ValueError):
                pass
    return targets


def resolve_bases(func: Func):
    """Walk the function tracking what each register provably holds.

    State values:
        ("sym", name, delta) - a known base plus a constant offset
        ("hi", value)        - upper half from lui, awaiting its lo half
        ("abs", addr)        - a fully resolved absolute address
        None                 - unknown, and we say so

    Everything not explicitly modelled invalidates the destination register.
    Conservative by construction: the cost of a missed base is a row marked
    'unknown'; the cost of a wrong base is a poisoned inference downstream.
    """
    targets = branch_targets(func)

    state: dict[str, tuple] = {
        "a0": ("sym", "param0", 0),
        "a1": ("sym", "param1", 0),
        "a2": ("sym", "param2", 0),
        "a3": ("sym", "param3", 0),
        "sp": ("sym", "stack", 0),
        "gp": ("sym", "gp", 0),
    }

    clobber_after_delay_slot = False
    resolutions: dict[int, tuple[str, int]] = {}

    for insn in func.insns:
        d = insn.decoded

        # A branch target merges paths we did not follow; drop everything that
        # is not structurally fixed.
        if insn.addr in targets:
            state = {k: v for k, v in state.items() if k in BRANCH_STABLE}
            state.setdefault("sp", ("sym", "stack", 0))
            state.setdefault("gp", ("sym", "gp", 0))

        # Resolve this instruction's access before applying its effects.
        if d.doesDereference():
            try:
                base_reg = str(d.rs).split(":")[-1].strip(" >").split()[0]
            except Exception:
                base_reg = "?"
            offset = d.getProcessedImmediate()
            held = state.get(base_reg)

            if held is None:
                resolutions[insn.addr] = ("unknown", offset)
            elif held[0] == "sym":
                resolutions[insn.addr] = (held[1], held[2] + offset)
            elif held[0] == "abs":
                resolutions[insn.addr] = (f"global:0x{held[1]:08X}", offset)
            elif held[0] == "hi":
                # lui/lo pair addressing a symbol directly; the lo half is this
                # instruction's own immediate, so the field offset is zero.
                resolutions[insn.addr] = (f"global:0x{held[1] + offset:08X}", 0)
            else:
                resolutions[insn.addr] = ("unknown", offset)

        # Apply register effects.
        if clobber_after_delay_slot:
            for reg in CALLER_SAVED:
                state.pop(reg, None)
            clobber_after_delay_slot = False

        if d.isFunctionCall():
            # The delay slot still executes with the pre-call state.
            clobber_after_delay_slot = True

        dest = _dest_register(d)
        if dest is None:
            continue

        op = d.getOpcodeName()
        src = _source_register(d)
        src_state = state.get(src) if src else None

        if op == "lui":
            state[dest] = ("hi", (d.getProcessedImmediate() & 0xFFFF) << 16)
        elif op in ("addiu", "addi") and src_state is not None:
            imm = d.getProcessedImmediate()
            if src_state[0] == "hi":
                state[dest] = ("abs", src_state[1] + imm)
            elif src_state[0] == "sym":
                state[dest] = ("sym", src_state[1], src_state[2] + imm)
            else:
                state.pop(dest, None)
        elif op == "ori" and src_state is not None and src_state[0] == "hi":
            state[dest] = ("abs", src_state[1] | (d.getProcessedImmediate() & 0xFFFF))
        elif op in ("move", "addu", "or") and d.maybeIsMove() and src_state is not None:
            state[dest] = src_state
        else:
            state.pop(dest, None)

    return resolutions


def _reg_name(reg) -> str | None:
    try:
        return str(reg).split(":")[-1].strip(" >").split()[0]
    except Exception:
        return None


def _dest_register(d) -> str | None:
    try:
        if d.modifiesRt():
            return _reg_name(d.rt)
        if d.modifiesRd():
            return _reg_name(d.rd)
    except RuntimeError:
        return None
    return None


def _source_register(d) -> str | None:
    try:
        if d.readsRs():
            return _reg_name(d.rs)
    except RuntimeError:
        return None
    return None


# --------------------------------------------------------------------- emit


def evidence_rows(func: Func, resolutions):
    """Yield evidence dicts for one function. Pure observation."""
    for insn in func.insns:
        d = insn.decoded
        op = d.getOpcodeName()

        if d.isFunctionCall():
            try:
                target = d.getInstrIndexAsVram()
            except (RuntimeError, ValueError):
                target = None
            yield {
                "kind": "call", "addr": insn.addr, "func_addr": func.addr,
                "op": op, "target_addr": target,
            }
            continue

        if not d.doesDereference():
            continue

        access = str(d.getAccessType()).split(":")[-1].split("(")[0].strip(" >")
        width = ACCESS_WIDTH.get(access)
        is_load = bool(d.doesLoad())
        base, offset = resolutions.get(insn.addr, ("unknown", d.getProcessedImmediate()))

        # A store carries no signedness. NULL, not 0 -- absence of information
        # is not the same as a claim of unsignedness.
        signed = (0 if d.doesUnsignedMemoryAccess() else 1) if is_load else None

        yield {
            "kind": "mem_access", "addr": insn.addr, "func_addr": func.addr,
            "op": op,
            "base": base,
            "base_reg": _reg_name(d.rs) or "?",
            "offset": offset,
            "width": width,
            "signed": signed,
            "class": "float" if d.isFloat() else "int",
            "access": "partial" if access in PARTIAL_ACCESS else "full",
            "is_load": int(is_load),
            "target_addr": None,
        }


# ------------------------------------------------------------------ map file


def parse_tu_map(map_path: Path) -> dict[str, str]:
    """Map symbol name -> translation unit, from the linker map.

    TU boundaries are recovered, not guessed: the linker laid each object out
    contiguously and recorded it here.
    """
    symbol_tu: dict[str, str] = {}
    current_obj: str | None = None

    section_re = re.compile(r"^\s+\.(text|rodata|data|bss)\s+0x([0-9a-f]+)\s+0x([0-9a-f]+)\s+(\S+)")
    symbol_re = re.compile(r"^\s+0x([0-9a-f]+)\s+(\S+)$")

    for line in map_path.read_text(errors="replace").splitlines():
        sec = section_re.match(line)
        if sec:
            current_obj = sec.group(4)
            continue
        sym = symbol_re.match(line)
        if sym and current_obj:
            symbol_tu[sym.group(2)] = current_obj

    return symbol_tu


# ---------------------------------------------------------------------- main


def extract(repo: Path, target: str, db_path: Path) -> None:
    if db_path.exists():
        raise ValueError('refusing to replace historical evidence; extract into a new database')
    rom_sha1 = verify_build(repo)
    print(f"build verified green: {rom_sha1}")

    elfs, maps = list(repo.glob('build/*.elf')),list(repo.glob('build/*.map'))
    if len(elfs)!=1 or len(maps)!=1:
        raise ValueError('requires unambiguous built ELF and link map')
    elf, map_file = elfs[0],maps[0]

    funcs = disassemble(elf)
    rom_bindings = bind_repo_rom(repo,funcs)
    symbol_tu = parse_tu_map(map_file)
    print(f"disassembled {len(funcs)} symbols from {elf.name}")

    conn = sqlite3.connect(db_path)
    conn.executescript((Path(__file__).parent.parent / "kb" / "schema.sql").read_text())

    versions = {
        'elf_sha256':hashlib.sha256(elf.read_bytes()).hexdigest(),
        'map_sha256':hashlib.sha256(map_file.read_bytes()).hexdigest(),
        'extractor_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'source_recovery_status':'not established by binary extraction',
        'range_policy':'ELF STT_FUNC extent; address-indexed raw words; zero words retained',
        'rom_function_bindings_sha256':hashlib.sha256(json.dumps(rom_bindings,sort_keys=True).encode()).hexdigest(),
        "rabbitizer": getattr(rabbitizer, "__version__", "?"),
        "objdump": subprocess.run([OBJDUMP, "--version"], capture_output=True,
                                  text=True).stdout.splitlines()[0],
    }
    cur = conn.execute(
        "INSERT INTO extraction (target, rom_sha1, elf_path, tool_versions, created_at)"
        " VALUES (?,?,?,?,?)",
        (target, rom_sha1, str(elf), json.dumps(versions), int(time.time())),
    )
    extraction_id = cur.lastrowid

    # Translation units
    tu_ids: dict[str, int] = {}
    for obj in sorted(set(symbol_tu.values())):
        cur = conn.execute(
            "INSERT OR IGNORE INTO tus (name, object_path) VALUES (?,?)", (obj, obj))
        tu_ids[obj] = conn.execute(
            "SELECT id FROM tus WHERE name = ?", (obj,)).fetchone()[0]

    n_ev = 0
    n_unknown = 0
    for func in funcs:
        obj = symbol_tu.get(func.name)
        conn.execute(
            "INSERT INTO functions"
            " (addr, name, tu_id, size, insn_count, is_leaf, state)"
            " VALUES (?,?,?,?,?,?,?)",
            (func.addr, func.name, tu_ids.get(obj), func.size,
             len(func.insns), int(func.is_leaf), "asm"),
        )

        resolutions = resolve_bases(func)
        for row in evidence_rows(func, resolutions):
            conn.execute(
                "INSERT INTO evidence"
                " (extraction_id, kind, addr, func_addr, op, base, base_reg, offset,"
                "  width, signed, class, access, is_load, target_addr)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (extraction_id, row["kind"], row["addr"], row["func_addr"], row["op"],
                 row.get("base"), row.get("base_reg"), row.get("offset"),
                 row.get("width"), row.get("signed"), row.get("class"),
                 row.get("access"), row.get("is_load"), row.get("target_addr")),
            )
            n_ev += 1
            if row.get("base") == "unknown":
                n_unknown += 1

    conn.commit()

    total_mem = conn.execute(
        "SELECT COUNT(*) FROM evidence WHERE kind='mem_access'").fetchone()[0]
    resolved = total_mem - n_unknown if total_mem else 0
    pct = (100.0 * resolved / total_mem) if total_mem else 0.0

    print(f"functions      : {len(funcs)}")
    print(f"translation units: {len(tu_ids)}")
    print(f"evidence rows  : {n_ev}")
    print(f"  mem_access   : {total_mem}")
    print(f"  base resolved: {resolved} ({pct:.1f}%)")
    print(f"  base unknown : {n_unknown}")
    conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--target", required=True)
    ap.add_argument("--db", required=True, type=Path)
    args = ap.parse_args()
    extract(args.repo.expanduser(), args.target, args.db.expanduser())


if __name__ == "__main__":
    main()
