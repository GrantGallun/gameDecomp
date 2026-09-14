"""Exactness-first residual packets for agent-guided source repair.

The compiler oracle's ``exact`` verdict is the only success condition.  This
module packages the diagnostics that help an agent choose its next experiment
without pretending that the weighted asm-differ score is a byte percentage.
Raw ``.text`` distance is diagnostic too: equal section bytes can still hide a
relocation mismatch, which the oracle will correctly reject.
"""

from __future__ import annotations

import json
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

from solver import cfg, compilefix, signals, workspace


@dataclass(frozen=True)
class ResidualPacket:
    compiled: bool
    exact: bool
    weighted_progress_score: float
    compiler_error_signature: str
    target_instructions: int | None
    candidate_instructions: int | None
    instruction_delta: int | None
    target_text_bytes: int | None
    candidate_text_bytes: int | None
    text_length_delta: int | None
    positional_byte_distance: int | None
    positional_equal_bytes: int | None
    changed_diff_lines: int
    faults: dict[str, int]
    first_difference: tuple[str, ...]
    frontend: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def render(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


def elf_text(path: Path | str | None) -> bytes | None:
    """Read ``.text`` from an ELF32/ELF64 object without extra dependencies.

    Returns ``None`` when the artifact is absent or is not a supported ELF.
    Bounds are checked before every slice so a partial build artifact cannot
    turn a diagnostic into an exception that kills the repair run.
    """
    if path is None:
        return None
    artifact = Path(path)
    if not artifact.is_file():
        return None
    data = artifact.read_bytes()
    if len(data) < 16 or data[:4] != b"\x7fELF":
        return None
    elf_class, encoding = data[4], data[5]
    endian = ">" if encoding == 2 else "<" if encoding == 1 else ""
    if not endian:
        return None
    try:
        if elf_class == 1:
            if len(data) < 52:
                return None
            shoff = struct.unpack_from(endian + "I", data, 32)[0]
            shentsize, shnum, shstrndx = struct.unpack_from(
                endian + "HHH", data, 46)
            name_at, offset_at, size_at, word = 0, 16, 20, "I"
        elif elf_class == 2:
            if len(data) < 64:
                return None
            shoff = struct.unpack_from(endian + "Q", data, 40)[0]
            shentsize, shnum, shstrndx = struct.unpack_from(
                endian + "HHH", data, 58)
            name_at, offset_at, size_at, word = 0, 24, 32, "Q"
        else:
            return None
        if not shentsize or not shnum or shstrndx >= shnum:
            return None

        def section(index: int) -> tuple[int, int, int] | None:
            base = shoff + index * shentsize
            needed = base + size_at + struct.calcsize(word)
            if base < 0 or needed > len(data):
                return None
            return (
                struct.unpack_from(endian + "I", data, base + name_at)[0],
                struct.unpack_from(endian + word, data, base + offset_at)[0],
                struct.unpack_from(endian + word, data, base + size_at)[0],
            )

        string_header = section(shstrndx)
        if string_header is None:
            return None
        _name, string_offset, string_size = string_header
        if string_offset + string_size > len(data):
            return None
        names = data[string_offset:string_offset + string_size]
        for index in range(shnum):
            header = section(index)
            if header is None:
                return None
            name_offset, offset, size = header
            if name_offset >= len(names):
                continue
            end = names.find(b"\0", name_offset)
            if end < 0:
                continue
            if names[name_offset:end] != b".text":
                continue
            if offset + size > len(data):
                return None
            return data[offset:offset + size]
    except (OSError, struct.error, OverflowError):
        return None
    return None


def _changed_lines(diff: str, limit: int = 24) -> tuple[str, ...]:
    rows = [line for line in (diff or "").splitlines()
            if line[:1] in {"+", "-"}
            and not line.startswith(("+++", "---"))]
    return tuple(rows[:limit])


def build(attempt: workspace.Attempt, *, target_asm: str = "",
          target_object: Path | str | None = None,
          candidate_object: Path | str | None = None) -> ResidualPacket:
    """Build a stable packet from one authoritative verifier result."""
    sig = signals.analyse(
        attempt.diff or "", attempt.score, attempt.exact, attempt.compiled)
    target_insns = (len(cfg.parse_assembly(target_asm)[0])
                    if target_asm else None)
    candidate_insns = None
    instruction_delta = None
    if attempt.compiled:
        instruction_delta = sig.instr_delta
        if target_insns is not None:
            candidate_insns = target_insns - instruction_delta

    target_bytes = elf_text(target_object)
    candidate_bytes = elf_text(candidate_object)
    byte_distance = equal_bytes = None
    target_size = len(target_bytes) if target_bytes is not None else None
    candidate_size = (len(candidate_bytes)
                      if candidate_bytes is not None else None)
    length_delta = None
    if target_size is not None and candidate_size is not None:
        length_delta = candidate_size - target_size
        overlap = min(target_size, candidate_size)
        overlap_diff = sum(
            left != right
            for left, right in zip(target_bytes[:overlap],
                                   candidate_bytes[:overlap]))
        byte_distance = overlap_diff + abs(length_delta)
        equal_bytes = overlap - overlap_diff

    faults = {
        "structural": sig.structural,
        "layout": sig.layout,
        "offset": sig.offset,
        "width": sig.width,
        "relocation": sig.reloc,
        "register_allocation": sig.regalloc,
        "ordering": sig.ordering,
        "immediate": sig.immediate,
    }
    return ResidualPacket(
        compiled=attempt.compiled,
        exact=attempt.exact,
        weighted_progress_score=attempt.score,
        compiler_error_signature=(
            compilefix.signature(attempt.compiler_stderr)
            if not attempt.compiled else ""),
        target_instructions=target_insns,
        candidate_instructions=candidate_insns,
        instruction_delta=instruction_delta,
        target_text_bytes=target_size,
        candidate_text_bytes=candidate_size,
        text_length_delta=length_delta,
        positional_byte_distance=byte_distance,
        positional_equal_bytes=equal_bytes,
        changed_diff_lines=sig.diff_lines,
        faults=faults,
        first_difference=_changed_lines(attempt.diff),
        frontend=attempt.frontend,
    )
