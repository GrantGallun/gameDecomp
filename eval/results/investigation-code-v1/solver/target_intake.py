"""Resolve assembly targets by symbol identity, not file basename.

Only assembly and the linker symbol map are inspected. SDK macro assembly is
classified separately: retaining it is not successful C reconstruction.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from solver import m2c_input


class AssemblyBackendRequired(RuntimeError):
    pass


@dataclass(frozen=True)
class TargetResolution:
    function: str
    symbol: str
    address: int | None
    path: Path
    kind: str


def rename_symbol(assembly: str, original: str, replacement: str) -> str:
    """Rename a whole assembler symbol, never strings/comments or substrings."""
    pieces = re.split(r'("(?:\\.|[^"\\])*"|/\*.*?\*/|#[^\n]*)', assembly, flags=re.S)
    symbol = re.compile(rf"(?<![\w.$]){re.escape(original)}(?![\w.$])")
    return "".join(symbol.sub(replacement, part) if index % 2 == 0 else part
                   for index, part in enumerate(pieces))


def resolve(repo: Path, function: str) -> TargetResolution:
    if not re.fullmatch(r"[A-Za-z_]\w*", function):
        raise ValueError("invalid function identifier")
    mapping = repo / "symbol_addrs.txt"
    symbols = {name: int(addr, 16) for name, addr in re.findall(
        r"(?m)^\s*([A-Za-z_]\w*)\s*=\s*(0x[0-9a-fA-F]+)\s*;",
        mapping.read_text(errors="replace") if mapping.exists() else "")}
    address = symbols.get(function)
    aliases = {function}
    if address is not None:
        aliases.update(name for name, addr in symbols.items() if addr == address)
    matches = []
    for directory, kind in (("asm", "disassembly"), ("src", "sdk_assembly")):
        for path in sorted((repo / directory).rglob("*.s")):
            text = path.read_text(errors="replace")
            labels = re.findall(r"(?m)^\s*glabel\s+([A-Za-z_]\w*)\b", text)
            if kind == "sdk_assembly":
                labels += re.findall(r"(?m)^\s*(?:LEAF|NESTED)\s*\(\s*([A-Za-z_]\w*)\b", text)
            for label in set(labels) & aliases:
                matches.append(TargetResolution(function, label, address, path, kind))
        # Prefer binary disassembly over macro source when both exist.
        if matches:
            break
    exact = [item for item in matches if item.symbol == function]
    matches = exact or matches
    if len(matches) != 1:
        raise RuntimeError(f"target resolution for {function}: {len(matches)} candidates; "
                           "refusing missing or ambiguous symbol identity")
    return matches[0]


def bootstrap_resolved(repo: Path, function: str) -> Path:
    """Prepare an assembly-only workspace atomically, without a reference TU.

    Symbol aliases are renamed consistently in the isolated target, preserving
    instruction/data expressions. The original file/hash/address and rename
    are retained in a receipt. Existing workspaces are never overwritten.
    """
    resolution = resolve(repo, function)
    if resolution.kind == "sdk_assembly":
        raise AssemblyBackendRequired(
            f"{function}: SDK macro assembly at {resolution.path.relative_to(repo)}; "
            "requires a separate assembly/hardware backend, not counted as C recovery")
    ws = repo / "nonmatchings" / function
    if ws.exists():
        raise RuntimeError(f"refusing to replace existing incomplete workspace: {ws}")
    assembler = next((shutil.which(name) for name in (
        "mips-linux-gnu-as", "mips64-elf-as", "mips64-linux-gnuabi64-as")
        if shutil.which(name)), None)
    if assembler is None:
        raise RuntimeError("no supported MIPS assembler found")
    helpers = repo / "tools/claude-decomp-env"
    original = resolution.path.read_bytes()
    assembly = original.decode("utf-8")
    if resolution.symbol != function:
        assembly = rename_symbol(assembly, resolution.symbol, function)
    if len(re.findall(r"(?m)^\s*glabel\s+", assembly)) != 1:
        raise RuntimeError("multi-function target needs explicit extraction; refusing whole-file target")
    target = "\n".join((
        (helpers / "prelude.inc").read_text(),
        (repo / "include/macro.inc").read_text(), assembly))
    ws.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{function}-intake-", dir=ws.parent) as temporary:
        stage = Path(temporary) / function
        stage.mkdir()
        (stage / "target.s").write_text(target)
        assembled = subprocess.run([
            assembler, "-EB", "-march=vr4300", "-mtune=vr4300", "-Iinclude",
            "-o", str(stage / "target.o"), str(stage / "target.s")],
            cwd=repo, text=True, capture_output=True, timeout=120)
        if assembled.returncode:
            raise RuntimeError("resolved target assembly failed: " + assembled.stderr[-1500:])
        for helper in helpers.iterdir():
            if helper.is_file():
                (stage / helper.name).symlink_to(helper.resolve())
        draft, draft_input = m2c_input.draft(repo, stage / "target.s")
        if draft.returncode == 0:
            (stage / "base.c").write_text('#include "common.h"\n\n' + draft.stdout)
        receipt = {**asdict(resolution), "path": str(resolution.path.relative_to(repo)),
                   "original_sha256": hashlib.sha256(original).hexdigest(),
                   "target_sha256": hashlib.sha256((stage / "target.s").read_bytes()).hexdigest(),
                   "draft_context": "assembly only", "m2c_returncode": draft.returncode,
                   "draft_input": draft_input}
        (stage / "target-resolution.json").write_text(json.dumps(receipt, indent=2))
        stage.rename(ws)
    return ws
