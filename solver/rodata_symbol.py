"""A literal the target reads through a named data symbol: the rodata-literal residual.

Single-function workspaces compare against a target object assembled from the split assembly, where every read-only
constant is a named data symbol (`lwc1 $f4,%lo(D_800E0A50)(at)`). A candidate that writes the literal
(`-1.33333333f`) gets a section-relative load from its own `.rodata` instead: identical instructions, a different
relocation, and the object certificate (rightly) differs. The target's reference is binary evidence, so this owner
rewrites the literal on the compiler-attributed source line into a read of that symbol, declared `extern`, and ONLY
when the symbol's bytes in the built ELF equal the literal's value. A value that does not match is never substituted.

Found 2026-09-25 (eval/results/operand-repair-20260925): 13 structurally-correct campaign functions carry this as their
only non-register residual, and about 145 frontier functions carry it among others. Motivating residual:
initControllerPakFileDeleteFlow, `configureViewport(..., -1.33333333f)` against `%lo(D_800E0A50)`.
"""
from __future__ import annotations

import re
import struct
from functools import lru_cache
from pathlib import Path

LOADS = {"lwc1": ("f32", 4), "ldc1": ("f64", 8), "lw": ("s32", 4)}
NAMED = re.compile(r"%lo\(([A-Za-z_]\w*)(\+0x[0-9a-fA-F]+)?\)")
SECTION = re.compile(r"%lo\(\.(?:rodata|data)(?:\+0x[0-9a-fA-F]+)?\)")
FLOAT = re.compile(r"(?<![\w.])(-?)((?:\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?|\d+[eE][-+]?\d+)[fF]?(?![\w.])")
INT = re.compile(r"(?<![\w.])(-?)(0[xX][0-9a-fA-F]+|\d+)[uUlL]*(?![\w.])")


@lru_cache(maxsize=4)
def _alloc_sections(elf: str) -> tuple:
    data = Path(elf).read_bytes()
    if data[:7] != b"\x7fELF\x01\x02\x01":
        raise ValueError("expected big-endian ELF32")
    header = struct.unpack_from(">HHIIIIIHHHHHH", data, 16)
    shoff, count = header[5], header[11]
    out = []
    for i in range(count):
        row = struct.unpack_from(">IIIIIIIIII", data, shoff + i * 40)
        if row[1] == 1 and row[2] & 2:                      # PROGBITS, SHF_ALLOC
            out.append((row[3], row[4], row[5]))            # vaddr, file offset, size
    return data, tuple(out)


def rom_bytes(elf: Path, vaddr: int, size: int) -> bytes | None:
    """`size` bytes at `vaddr` in the built ELF's allocated sections, or None if not wholly inside one."""
    data, sections = _alloc_sections(str(elf))
    for addr, offset, length in sections:
        if addr <= vaddr and vaddr + size <= addr + length:
            return data[offset + vaddr - addr:offset + vaddr - addr + size]
    return None


def symbol_address(name: str, map_text: str = "") -> int | None:
    m = re.fullmatch(r"D_([0-9A-Fa-f]{8})", name)
    if m:
        return int(m.group(1), 16)
    m = re.search(rf"^\s*0x([0-9a-fA-F]{{8,16}})\s+{re.escape(name)}\s*$", map_text, re.M)
    return int(m.group(1), 16) & 0xFFFFFFFF if m else None


def _value_bytes(kind: str, token: str, negative: bool) -> bytes | None:
    try:
        if kind == "f32":
            return struct.pack(">f", -float(token) if negative else float(token))
        if kind == "f64":
            return struct.pack(">d", -float(token) if negative else float(token))
        value = int(token, 0)
        return struct.pack(">i", -value) if negative else struct.pack(">I", value & 0xFFFFFFFF)
    except (ValueError, OverflowError, struct.error):
        return None


def _replace_on_line(source: str, line: int, kind: str, want: bytes, symbol: str) -> str | None:
    lines = source.split("\n")
    if not 1 <= line <= len(lines):
        return None
    text = lines[line - 1]
    pattern = FLOAT if kind in ("f32", "f64") else INT
    for m in pattern.finditer(text):
        before = text[:m.start()].rstrip()
        unary = bool(m.group(1)) and (not before or before[-1] in "(,=+-*/?:<>!&|^~[")
        # The target reads the symbol and uses it as-is (the aligned instructions carry no negation), so the WHOLE
        # literal, a unary minus included, becomes the symbol when its magnitude matches the ROM bytes. Measured
        # 2026-09-25 on initControllerPakFileDeleteFlow: the draft's `-1.33333333f` was sign-flipped against the
        # ROM's +4/3; the object comparison could not see it because the constant lives in rodata. A binary minus
        # (`a - 1.0f`) is an operator and stays.
        if unary and _value_bytes(kind, m.group(2), False) == want:
            lines[line - 1] = text[:m.start()] + symbol + text[m.end():]
            return "\n".join(lines)
        if unary and _value_bytes(kind, m.group(2), True) == want:
            lines[line - 1] = text[:m.start()] + symbol + text[m.end():]
            return "\n".join(lines)
        if not unary and _value_bytes(kind, m.group(2), False) == want:
            lines[line - 1] = text[:m.start(2) if m.group(1) else m.start()] + symbol + text[m.end():]
            return "\n".join(lines)
    return None


def _declare(source: str, function: str, symbol: str, kind: str) -> str:
    if re.search(rf"\b{re.escape(symbol)}\s*;", source) and re.search(rf"\bextern\b[^;\n]*\b{re.escape(symbol)}\b", source):
        return source
    at = re.search(rf"^[^\n;{{}}]*\b{re.escape(function)}\s*\(", source, re.M)
    at = at.start() if at else 0
    return source[:at] + f"extern {kind} {symbol};\n" + source[at:]


TARGET_SECTION = re.compile(r"%lo\(\.(late_rodata|rodata)(?:\+(0x[0-9a-fA-F]+))?\)")


def literal_text(kind: str, value: bytes) -> str:
    """The shortest decimal literal that the compiler rounds to exactly `value`."""
    if kind == "f32":
        v = struct.unpack(">f", value)[0]
        for digits in range(1, 10):
            text = f"{v:.{digits}g}"
            if struct.pack(">f", float(text)) == value:
                break
        text = text if any(ch in text for ch in ".e") else text + ".0"
        return text + "f"
    if kind == "f64":
        text = repr(struct.unpack(">d", value)[0])
        return text if any(ch in text for ch in ".e") else text + ".0"
    return str(struct.unpack(">i", value)[0])


def _section_bytes(obj: Path, section: str) -> bytes | None:
    from solver import byte_certificate
    try:
        return byte_certificate.progbits_contents(Path(obj).read_bytes()).get(section)
    except (OSError, ValueError, struct.error):
        return None


def _value_literal(source: str, line: int, kind: str, want: bytes) -> str | None:
    """Rewrite the one float literal on `line` (or the one whose magnitude matches) to the value `want` encodes."""
    lines = source.split("\n")
    if not 1 <= line <= len(lines) or kind not in ("f32", "f64"):
        return None
    text = lines[line - 1]
    found = list(FLOAT.finditer(text))
    target = struct.unpack(">f" if kind == "f32" else ">d", want)[0]
    same_mag = [m for m in found if abs(float(m.group(2))) == abs(target) or
                _value_bytes(kind, m.group(2), False) == _value_bytes(kind, literal_text(kind, want).rstrip("f").lstrip("-"), False)]
    pick = same_mag if len(same_mag) == 1 else found if len(found) == 1 else []
    if not pick:
        return None
    m = pick[0]
    before = text[:m.start()].rstrip()
    unary = bool(m.group(1)) and (not before or before[-1] in "(,=+-*/?:<>!&|^~[")
    start = m.start() if unary or not m.group(1) else m.start(2)
    new = literal_text(kind, want)
    if not unary and m.group(1) and new.startswith("-"):
        return None                                      # a binary minus in front of a negative constant: ambiguous
    replaced = text[:start] + new + text[m.end():]
    if replaced == text:
        return None
    lines[line - 1] = replaced
    return "\n".join(lines)


def variants(source: str, function: str, diff: str, attribution: dict | None, *, elf: Path, map_text: str = "",
             target_obj: Path | None = None):
    """`(label, candidate)`: all stated rodata-literal sites at once, then each alone. Nothing without a verified,
    source-bound attribution and binary evidence for the value: a named target symbol (ROM bytes from the ELF) or a
    section-relative target reference (bytes from the target object's own `.late_rodata`/`.rodata`)."""
    from solver import evidence_site
    from solver.source_attribution import sha
    if not diff or not attribution or attribution.get("status") != "verified" \
            or attribution.get("source_sha256") != sha(source):
        return
    edits, literal_edits = [], []
    for sig, line, target, cand in evidence_site.sites(diff, attribution):
        if line is None or not target or not cand:
            continue
        op = target.split()[0]
        if op not in LOADS:
            continue
        section = TARGET_SECTION.search(target)
        if section:                                    # section-relative target reference: value from the target object
            if target_obj is None:
                continue
            kind, size = LOADS[op]
            data = _section_bytes(target_obj, "." + section.group(1))
            offset = int(section.group(2), 16) if section.group(2) else 0
            if data is not None and offset + size <= len(data):
                literal_edits.append((line, kind, data[offset:offset + size]))
            continue
        named = NAMED.search(target)
        if sig != "field:symbol" or not named or named.group(2) or not SECTION.search(cand):
            continue
        kind, size = LOADS[op]
        symbol = named.group(1)
        address = symbol_address(symbol, map_text)
        want = rom_bytes(elf, address, size) if address is not None else None
        if want is None:
            continue
        edits.append((line, kind, want, symbol))
        literal_edits.append((line, kind, want))      # the literal form too: correct value, the compiler's own rodata
    seen = set()
    for line, kind, want in literal_edits:
        changed = _value_literal(source, line, kind, want)
        if changed and changed not in seen:
            seen.add(changed)
            yield f"rodata_literal:{literal_text(kind, want)}@{line}", changed
    singles = []
    for line, kind, want, symbol in edits:
        changed = _replace_on_line(source, line, kind, want, symbol)
        if changed:
            singles.append((f"rodata_symbol:{symbol}@{line}", _declare(changed, function, symbol, kind)))
    if len(singles) > 1:
        combined = source
        for line, kind, want, symbol in edits:
            step = _replace_on_line(combined, line, kind, want, symbol)
            if step:
                combined = _declare(step, function, symbol, kind)
        if combined != source:
            yield "rodata_symbol:all", combined
    yield from singles
