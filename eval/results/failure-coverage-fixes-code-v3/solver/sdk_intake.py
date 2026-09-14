"""Narrow ROM-derived SDK intake. Hardware instructions remain explicit blockers.

No reference C or SDK macro bodies enter candidate generation. The first backend
supports relocation-free straight-line routines ending in jr ra + delay slot.
Reassembly must reproduce the complete ROM slice before exposing an oracle.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
from collections import Counter

from solver import m2c_input, residual


class UnsupportedTarget(RuntimeError):
    def __init__(self, report: dict):
        self.report = report
        super().__init__(json.dumps(report, sort_keys=True))


def rom_offset(config: dict, address: int, size: int, rom_size: int) -> int:
    if size <= 0 or size % 4 or address % 4:
        raise ValueError("invalid instruction range")
    segments = config["segments"]
    candidates = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict) or segment.get("type") != "code":
            continue
        if not all(isinstance(segment.get(k), int) for k in ("start", "vram")):
            continue
        following = segments[index + 1:]
        ends = [s.get("start") if isinstance(s, dict) else s[0]
                for s in following if isinstance(s, (dict, list))]
        end = next((v for v in ends if isinstance(v, int)), rom_size)
        offset = address - segment["vram"] + segment["start"]
        if segment["start"] <= offset and offset + size <= min(end, rom_size):
            candidates.append(offset)
    if len(candidates) != 1:
        raise ValueError("missing or ambiguous file-backed ROM mapping")
    return candidates[0]


def instructions(disassembly: str, address: int, size: int) -> list[dict]:
    rows = []
    for line in disassembly.splitlines():
        match = re.match(r"\s*([0-9a-f]+):\s+([0-9a-f]{8})\s+([\w.]+)\s*(.*?)\s*$", line)
        if match:
            pc, word, opcode, operands = match.groups()
            rows.append(dict(pc=int(pc, 16), word=word, opcode=opcode, operands=operands))
    if [r["pc"] for r in rows] != list(range(address, address + size, 4)):
        raise ValueError("disassembler did not cover every instruction")
    return rows


def classify(rows: list[dict]) -> dict:
    inventory = {'instruction_count':len(rows),
                 'opcode_counts':dict(sorted(Counter(r['opcode'] for r in rows).items()))}
    hardware = {"mfc0", "mtc0", "dmfc0", "dmtc0", "cfc1", "ctc1", "cache",
                "tlbp", "tlbr", "tlbwi", "tlbwr", "eret", "syscall", "break", "sync"}
    hazards = [r for r in rows if r["opcode"] in hardware]
    if hazards:
        return {**inventory, "status": "hardware_backend_required", "instructions": hazards,
                "reason": "ordinary C backend cannot express these machine operations"}
    allowed = {"nop", "move", "li", "addiu", "addu", "subu", "and", "andi", "or", "ori",
               "xor", "xori", "nor", "sll", "srl", "sra", "sllv", "srlv", "srav",
               "slt", "sltu", "slti", "sltiu", "lb", "lbu", "lh", "lhu", "lw",
               "sb", "sh", "sw"}
    terminal_ok = len(rows) >= 2 and rows[-2]['opcode'] == 'jr' and rows[-2]['operands'] == 'ra'
    unsupported = [r for index,r in enumerate(rows)
                   if r['opcode'] not in allowed and not (terminal_ok and index == len(rows)-2)]
    if not terminal_ok or unsupported:
        return {**inventory, "status": "sdk_control_flow_or_relocation_unsupported",
                'terminal_return_shape_valid':terminal_ok,'terminal_instructions':rows[-2:],
                'unsupported_opcode_counts':dict(sorted(Counter(r['opcode'] for r in unsupported).items())),
                'unsupported_instructions':unsupported[:16],
                'omitted_unsupported_instructions':max(0,len(unsupported)-16),
                'next_action':'Audit recorded control flow/opcodes and range/relocation requirements; '
                              'extend ROM reassembly admission and execution only with byte/semantic controls. '
                              'This is a backend dialect limit, not proof that C reconstruction is impossible.',
                "reason": "backend currently requires straight-line ordinary instructions and jr ra"}
    # Absolute address construction / gp-relative accesses need relocation
    # reconstruction, not a misleading absolute immediate oracle.
    special = [r for r in rows if re.search(r"\b(?:gp|k0|k1)\b", r["operands"])]
    if special:
        return {**inventory, "status": "sdk_abi_or_relocation_unsupported",
                'instructions':special,'reason': "special register use"}
    return {"status": "ordinary_straight_line", "instruction_count": len(rows)}


def render(function: str, rows: list[dict]) -> str:
    registers = r"\b(zero|at|v[01]|a[0-3]|t[0-9]|s[0-8]|k[01]|gp|sp|fp|ra)\b"
    lines = [".set noreorder", ".set noat", ".text", ".balign 4", f"glabel {function}"]
    for row in rows:
        operands = re.sub(registers, lambda m: "$" + m.group(), row["operands"])
        lines.append(f'/* {row["pc"]:08X} {row["word"].upper()} */ {row["opcode"]} {operands}')
    lines.append(f"endlabel {function}")
    return "\n".join(lines) + "\n"


def bootstrap(repo: Path, db: Path, function: str) -> Path:
    import yaml
    if not re.fullmatch(r"[A-Za-z_]\w*", function):
        raise ValueError("invalid function identifier")
    ws = repo / "nonmatchings" / function
    if ws.exists():
        raise ValueError("refusing to replace an existing SDK workspace")
    config = yaml.safe_load((repo / "snowboardkids.yaml").read_text())
    rom = (repo / config["options"]["target_path"]).read_bytes()
    if hashlib.sha1(rom).hexdigest() != config["sha1"]:
        raise ValueError("ROM identity differs from extraction configuration")
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT addr,size FROM functions WHERE name=?", (function,)).fetchall()
    if len(rows) != 1:
        raise ValueError("missing or ambiguous binary function range")
    address, size = rows[0]
    offset = rom_offset(config, address, size, len(rom))
    raw = rom[offset:offset + size]
    report = {"function": function, "address": address, "rom_offset": offset,
              "bytes": size, "rom_sha1": config["sha1"],
              "slice_sha256": hashlib.sha256(raw).hexdigest(), "reference_source_read": False}
    helpers = repo / "tools/claude-decomp-env"
    ws.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{function}-rom-", dir=ws.parent) as temporary:
        stage = Path(temporary) / function
        stage.mkdir()
        binary = stage / "original.bin"
        binary.write_bytes(raw)
        disasm = subprocess.run(["mips-linux-gnu-objdump", "-D", "-z", "-b", "binary",
            "-m", "mips:4300", "-EB", f"--adjust-vma={address}", str(binary)],
            check=True, capture_output=True, text=True, timeout=30).stdout
        decoded = instructions(disasm, address, size)
        report.update(classify(decoded))
        if report["status"] != "ordinary_straight_line":
            raise UnsupportedTarget(report)
        target = "\n".join(((helpers / "prelude.inc").read_text(),
                           (repo / "include/macro.inc").read_text(), render(function, decoded)))
        (stage / "target.s").write_text(target)
        subprocess.run(["mips-linux-gnu-as", "-EB", "-march=vr4300", "-mtune=vr4300",
            "-Iinclude", "-o", str(stage / "target.o"), str(stage / "target.s")],
            cwd=repo, check=True, capture_output=True, text=True, timeout=30)
        if residual.elf_text(stage / "target.o") != raw:
            raise ValueError("reassembled SDK text differs from ROM (including alignment/padding)")
        for helper in helpers.iterdir():
            if helper.is_file():
                (stage / helper.name).symlink_to(helper.resolve())
        draft, metadata = m2c_input.draft(repo, stage / "target.s")
        if draft.returncode == 0:
            (stage / "base.c").write_text('#include "common.h"\n' + draft.stdout)
        report.update(reassembled_rom_slice_exact=True, m2c_returncode=draft.returncode,
                      m2c=metadata, target_sha256=hashlib.sha256(target.encode()).hexdigest())
        (stage / "target-resolution.json").write_text(json.dumps(report, indent=2))
        stage.rename(ws)
    return ws
