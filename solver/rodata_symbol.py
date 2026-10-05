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


# ---------------------------------------------------------------------------------------------------------------------
# Address-taken rodata: a string (or any datum) whose ADDRESS the function passes on (`lui`/`addiu`), not a value it
# loads. The branch above only covers loads, and it reads the diff; this one reads the two objects, because the
# normalized diff is exactly where this residual hides (eval/results/hidden-object-20260930: .text byte-identical,
# score 99.9-100). Schema 3 refuses candidate-owned address-taken rodata ("needs its named symbol for integration")
# after drawRaceSplitscreenSelectEntryFee's own literal failed the whole-ROM checksum (2026-09-14), and admits a
# candidate extern named like the target's rodata label -- so the edit names the target's label at each site.
# The label comes from the target object at the same instruction offset: binary evidence, never a guess.

ADDIU = 0x09
_CSTR = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
_ESC = {"n": 10, "t": 9, "r": 13, "0": 0, "a": 7, "b": 8, "f": 12, "v": 11, "\\": 92, '"': 34, "'": 39, "?": 63}


def c_string(body: str) -> bytes | None:
    """Bytes of a C string literal body (escapes decoded), or None for escapes this does not model."""
    out, i = bytearray(), 0
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out += ch.encode("latin-1")
            i += 1
            continue
        nxt = body[i + 1]
        octal = re.match(r"[0-7]{1,3}", body[i + 1:])
        if octal:
            out.append(int(octal.group(0), 8) & 0xFF)
            i += 1 + len(octal.group(0))
        elif nxt == "x":
            hexa = re.match(r"[0-9a-fA-F]+", body[i + 2:])
            if not hexa:
                return None
            out.append(int(hexa.group(0), 16) & 0xFF)
            i += 2 + len(hexa.group(0))
        elif nxt in _ESC:
            out.append(_ESC[nxt])
            i += 2
        else:
            return None
    return bytes(out)


def address_facts(target_obj: bytes, candidate_obj: bytes) -> dict:
    """The object evidence the rewrite needs, as plain data (so it can be logged and replayed in tests).

    sites: one row per candidate HI16/LO16 `addiu` pair whose target counterpart, at the SAME instruction offsets,
    addresses a target rodata label exactly: {at, target_label, candidate: ["external", name] | ["section", offset]}.
    candidate_symbols: named candidate rodata data {name: offset}; candidate_rodata: hex of the candidate's .rodata."""
    from solver import byte_certificate as bc, function_boundary as fb
    tsec, csec = bc.object_image(target_obj)["sections"], bc.object_image(candidate_obj)["sections"]
    if ".text" not in tsec or ".text" not in csec or tsec[".text"]["size"] != csec[".text"]["size"]:
        return {"sites": [], "declined": "text sizes differ"}
    ttext, ctext = bc.section_contents(target_obj)[".text"], bc.section_contents(candidate_obj)[".text"]
    size = len(ctext)
    # Symbol renames are confirmed only where the instructions already match; elsewhere an offset is not a site.
    same_text = ttext == ctext
    labels: dict[tuple, list] = {}
    for section, rows in fb._data_symbols(target_obj).items():
        for value, name in rows:
            if "." not in name:
                labels.setdefault((section, value), []).append(name)

    def pairs(rows):
        out, rows = {}, [(at, kind, tuple(identity)) for at, kind, identity in rows]
        for index, (at, kind, identity) in enumerate(rows):
            if kind == 5 and index + 1 < len(rows) and rows[index + 1][1] == 6 and rows[index + 1][2] == identity:
                out[at] = ((at, kind, identity), rows[index + 1])
        return out
    tpairs, cpairs = pairs(tsec[".text"]["relocations"]), pairs(csec[".text"]["relocations"])
    sites = []
    for at, cgroup in sorted(cpairs.items()):
        tgroup = tpairs.get(at)
        if tgroup is None or tgroup[1][0] != cgroup[1][0] or not 0 <= cgroup[1][0] < size:
            continue
        tid, cid = tgroup[0][2], cgroup[0][2]
        if tid[0] == "external" and cid[0] == "external" and tid[1] != cid[1] and same_text:
            # A different external at the same instruction offsets: the target's name is the evidence, whatever the
            # instruction does with it. drawCharacterSelectCourseExitPreviewPanel (2026-09-30): the candidate's
            # gCharacterSelectCourseExitPreviewCornerTile, which the ROM does not define, renamed to the target's
            # gCharacterSelectCourseExitPreviewData -> object exact.
            sites.append({"at": at, "target_label": tid[1], "candidate": ["external", cid[1]], "symbol": True})
            continue
        if tid[0] != "section" or tid[1] not in fb.DATA_SECTIONS:
            continue
        low = cgroup[1][0]
        if int.from_bytes(ctext[low:low + 4], "big") >> 26 != ADDIU or \
                int.from_bytes(ttext[low:low + 4], "big") >> 26 != ADDIU:
            continue                                      # loads belong to `variants` above
        datum = tid[2] + fb._addend(ttext, tgroup)
        names = labels.get((tid[1], datum), [])
        if len(names) != 1:
            continue                                      # no exact label, or an ambiguous one: never guess
        if cid[0] == "external":
            candidate = ["external", cid[1]]
        elif cid[0] == "section" and cid[1] in fb.DATA_SECTIONS:
            candidate = ["section", cid[2] + fb._addend(ctext, cgroup)]
        else:
            continue
        sites.append({"at": at, "target_label": names[0], "candidate": candidate})
    symbols = {name: value for value, name in fb._data_symbols(candidate_obj).get(".rodata", []) if "." not in name}
    rodata = bc.section_contents(candidate_obj).get(".rodata", b"")
    return {"sites": sites, "candidate_symbols": symbols, "candidate_rodata": rodata.hex()}


def _definition(source: str, name: str):
    """The one file-scope `const char NAME[..] = "...";` statement, as a match, or None."""
    found = list(re.finditer(rf'^[ \t]*(?:static[ \t]+)?const[ \t]+(?:char|u8|s8)[ \t]+{re.escape(name)}[ \t]*'
                             rf'\[[^\]\n]*\][ \t]*=[ \t]*"(?:[^"\\\n]|\\.)*"[ \t]*;[ \t]*\n', source, re.M))
    return found[0] if len(found) == 1 else None


def _rename(source: str, old: str, new: str) -> str:
    return re.sub(rf"\b{re.escape(old)}\b", new, source)


def address_rewrite(source: str, function: str, facts: dict) -> tuple[str | None, dict]:
    """Name every address-taken rodata site after the target's label. Returns (source or None, receipt)."""
    receipt: dict = {"renamed": {}, "definitions": {}, "literals": [], "dropped_unread": [], "declined": []}
    sites = facts.get("sites") or []
    if not sites:
        receipt["declined"].append(facts.get("declined") or "no address-taken rodata site with an exact target label")
        return None, receipt
    rodata = bytes.fromhex(facts.get("candidate_rodata") or "")
    by_offset = {v: n for n, v in (facts.get("candidate_symbols") or {}).items()}
    out, labels, read = source, set(), set()
    anonymous = []                                           # (candidate offset, label) for unnamed literals
    for site in sites:
        label, (kind, what) = site["target_label"], site["candidate"]
        if not site.get("symbol"):
            labels.add(label)                  # a renamed data symbol keeps its own declaration's type
        if kind == "external":
            if what != label:
                if receipt["renamed"].get(what, label) != label:
                    receipt["declined"].append(f"{what} names two labels")
                    return None, receipt
                receipt["renamed"][what] = label
            continue
        read.add(what)
        name = by_offset.get(what)
        if name is None:
            anonymous.append((what, label))
        elif receipt["definitions"].get(name, label) != label:
            receipt["declined"].append(f"{name} names two labels")
            return None, receipt
        else:
            receipt["definitions"][name] = label
    for name, label in receipt["definitions"].items():
        m = _definition(out, name)
        if m is None:
            receipt["declined"].append(f"no single-line definition of {name}")
            return None, receipt
        out = out[:m.start()] + f"extern const char {label}[];\n" + out[m.end():]
        out = _rename(out, name, label)
    for old, label in receipt["renamed"].items():
        out = _rename(out, old, label)
    if anonymous:
        # Pair each unnamed datum with its literal by reproducing the layout, never by searching for the value: the
        # source's string literals, in order and 4-aligned, must rebuild the candidate's .rodata byte for byte (only
        # zero padding after). Anything else in .rodata -- named data, floats, tables -- and this declines.
        if by_offset:
            receipt["declined"].append("anonymous literal beside named rodata: layout not modelled")
            return None, receipt
        literals = [m for m in _CSTR.finditer(out)
                    if not out[max(0, out.rfind("\n", 0, m.start()) + 1):m.start()].lstrip().startswith("#")]
        layout, at = {}, 0
        for m in literals:
            value = c_string(m.group(1))
            if value is None:
                receipt["declined"].append("string escape not modelled")
                return None, receipt
            if rodata[at:at + len(value) + 1] != value + b"\0":
                receipt["declined"].append(f"source literals do not reproduce .rodata at +{at}")
                return None, receipt
            layout[at] = m
            at = (at + len(value) + 1 + 3) & ~3
        if any(rodata[at:]):
            receipt["declined"].append("source literals do not account for all of .rodata")
            return None, receipt
        edits = []
        for offset, label in sorted(anonymous):
            m = layout.get(offset)
            if m is None:
                receipt["declined"].append(f"no literal starts at .rodata+{offset}")
                return None, receipt
            edits.append((m.start(), m.end(), label))
            receipt["literals"].append({"offset": offset, "value": c_string(m.group(1)).decode("latin-1"),
                                        "label": label})
        for start, end, label in sorted(edits, reverse=True):
            out = out[:start] + label + out[end:]
    # Named candidate data the function never addresses still lands in its .rodata ("candidate rodata the function
    # does not read"). Drop the definition only where every other mention follows a `#define NAME` that redirects it.
    for name, offset in (facts.get("candidate_symbols") or {}).items():
        if offset in read or name in receipt["definitions"]:
            continue
        m = _definition(out, name)
        if m is None:
            continue
        rest = out[:m.start()] + out[m.end():]
        define = re.search(rf"^[ \t]*#[ \t]*define[ \t]+{re.escape(name)}\b", rest, re.M)
        mentions = [x.start() for x in re.finditer(rf"\b{re.escape(name)}\b", rest)]
        if define and all(pos >= define.start() for pos in mentions):
            out = rest
            receipt["dropped_unread"].append(name)
    for label in sorted(labels):
        if not re.search(rf"\bextern\b[^;\n]*\b{re.escape(label)}\b", out):
            at = re.search(rf"^[^\n;{{}}]*\b{re.escape(function)}\s*\(", out, re.M)
            at = at.start() if at else 0
            out = out[:at] + f"extern const char {label}[];\n" + out[at:]
    if out == source:
        receipt["declined"].append("nothing to change")
        return None, receipt
    return out, receipt


def address_variants(source: str, function: str, *, target_obj: Path, candidate_obj: Path):
    """`(label, candidate)` for the address-taken branch; nothing unless both objects exist and a site pairs."""
    try:
        facts = address_facts(Path(target_obj).read_bytes(), Path(candidate_obj).read_bytes())
    except (OSError, ValueError, KeyError, IndexError, struct.error):
        return
    changed, receipt = address_rewrite(source, function, facts)
    if changed:
        labels = sorted({s["target_label"] for s in facts["sites"]})
        yield f"rodata_address:{labels[0]}" + (f"+{len(labels) - 1}" if len(labels) > 1 else ""), changed
