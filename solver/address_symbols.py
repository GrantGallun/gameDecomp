"""Address literals the target spells as linker symbols: `(void *)0x593D10` -> `(void *)&D_593D10`.

Measured on the 2026-09-14 failure census: 28 pending functions pass ROM asset
ranges as integer literals, 304 sites in total (loadRaceCourseAssets alone has
64). IDO loads a literal with `lui/ori` but a symbol with `lui/addiu %lo`, so each
literal costs two instruction faults plus two relocation faults, and only the
symbol form can match.

The address of each target symbol comes from the object diff itself. The aligned
candidate line spells the value: `lui a0,0x59` against `%hi(D_593D10)`, and
`ori a0,a0,0x3d10` or `addiu`/`N(reg)` against `%lo(D_593D10)`. Splat's `D_<hex>`
names also state their address. Dereferenced hardware registers
(`*(volatile u32 *)0xA4800000`) are deliberately left alone: they already link
to the same bytes, and the literal is how libultra's IO macros expand.
"""
from __future__ import annotations

import re

from solver import repair_context

RELOC = re.compile(r"%(?P<part>hi|lo)\((?P<sym>[A-Za-z_]\w*)\)")
NAMED_ADDRESS = re.compile(r"^D_(?P<hex>[0-9A-Fa-f]{5,8})$")
LITERAL = re.compile(r"(?<![\w.])(?P<lit>0[xX][0-9a-fA-F]+|\d+)[uUlL]*(?![\w.])")


def _operands(line: str) -> tuple[str, list[str]]:
    parts = line.strip().split(None, 1)
    return (parts[0], [o.strip() for o in parts[1].split(",")]) if len(parts) > 1 else (parts[0] if parts else "", [])


def _hunks(diff: str):
    removed, added = [], []
    for line in diff.splitlines() + [" "]:
        if line.startswith(("---", "+++", "@@")):
            continue
        if line.startswith("-"):
            removed.append(line[1:])
        elif line.startswith("+"):
            added.append(line[1:])
        else:
            if removed or added:
                yield removed, added
            removed, added = [], []


def _signed16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def addresses(diff: str) -> dict[str, int]:
    """Symbol -> address, read from target relocations aligned with candidate literals."""
    his, los, found = {}, {}, {}
    for removed, added in _hunks(diff):
        for want in removed:
            for match in RELOC.finditer(want):
                named = NAMED_ADDRESS.match(match.group("sym"))
                if named:
                    found[match.group("sym")] = int(named.group("hex"), 16)
        if len(removed) != len(added):
            continue
        for want, got in zip(removed, added):
            reloc = RELOC.search(want)
            if not reloc or RELOC.search(got):
                continue
            symbol = reloc.group("sym")
            wm, wo = _operands(want)
            gm, go = _operands(got)
            try:
                if reloc.group("part") == "hi" and gm == "lui" and wm == "lui" and len(go) == 2:
                    his.setdefault(symbol, set()).add(int(go[1], 0))
                elif reloc.group("part") == "lo":
                    if gm == "ori" and len(go) == 3:
                        los.setdefault(symbol, set()).add(int(go[2], 0))
                    elif gm == wm == "addiu" and len(go) == 3:
                        los.setdefault(symbol, set()).add(_signed16(int(go[2], 0) & 0xFFFF))
                    elif gm == wm and go and "(" in go[-1]:
                        los.setdefault(symbol, set()).add(_signed16(int(go[-1].split("(")[0] or "0", 0) & 0xFFFF))
            except ValueError:
                continue
    for symbol in his.keys() & los.keys():
        if len(his[symbol]) == 1 and len(los[symbol]) == 1 and symbol not in found:
            found[symbol] = ((next(iter(his[symbol])) << 16) + next(iter(los[symbol]))) & 0xFFFFFFFF
    return found


# A literal directly under a pointer cast that is not dereferenced: `(void *)0x593D10`, `(u8 *) 0x80160480`.
POINTER_CAST = re.compile(r"(?<![*\w])\(\s*(?:const\s+)?[A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*\s*\*\s*\)\s*"
                          r"(?P<lit>0[xX][0-9a-fA-F]+|\d+)[uUlL]*(?![\w.])")


def _dereferenced(body: str, start: int) -> bool:
    before = body[:start].rstrip()
    return before.endswith("*") or before.endswith("*(") or re.search(r"\*\s*\(\s*$", before) is not None


def segments_from(config: dict) -> dict[int, tuple[list[str], list[str]]]:
    """address -> (segments starting there, segments ending there), from a splat segment config."""
    starts = []
    for segment in config.get("segments", []):
        start, name = ((segment.get("start"), segment.get("name")) if isinstance(segment, dict) else
                       (segment[0], segment[2] if len(segment) > 2 else None) if isinstance(segment, list) and segment
                       else (None, None))
        if isinstance(start, int):
            starts.append((start, name if isinstance(name, str) else None))
    table: dict[int, tuple[list[str], list[str]]] = {}
    for index, (start, name) in enumerate(starts):
        if not name:
            continue
        table.setdefault(start, ([], []))[0].append(name)
        end = next((s for s, _ in starts[index + 1:] if s > start), None)
        if end is not None:
            table.setdefault(end, ([], []))[1].append(name)
    return table


def _link_name(address: int, previous: tuple[int, str] | None, segments: dict) -> str | None:
    """The segment-bound symbol the real link defines for an address, if unambiguous."""
    starting, ending = segments.get(address, ([], []))
    if previous is not None and previous[1] in ending:
        return previous[1] + "_ROM_END"            # end of the range the preceding literal began
    if len(starting) == 1:
        return starting[0] + "_ROM_START"
    if len(ending) == 1:
        return ending[0] + "_ROM_END"
    return None


RODATA_LO = re.compile(r"%lo\(\.rodata(?:\+(?P<off>0x[0-9a-fA-F]+|\d+))?\)")
STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"')
ESCAPE = re.compile(r"\\(?:x[0-9a-fA-F]+|[0-7]{1,3}|.)")


def rodata_names(diff: str) -> dict[int, str]:
    """Candidate `.rodata` offset -> the target's symbol, from aligned address-taken `addiu %lo` sites."""
    found, conflicts = {}, set()
    for removed, added in _hunks(diff):
        if len(removed) != len(added):
            continue
        for want, got in zip(removed, added):
            wanted, own = RELOC.search(want), RODATA_LO.search(got)
            if not (wanted and own) or wanted.group("part") != "lo" or not (_operands(want)[0] == _operands(got)[0] == "addiu"):
                continue
            offset = int(own.group("off") or "0", 0)
            if found.setdefault(offset, wanted.group("sym")) != wanted.group("sym"):
                conflicts.add(offset)
    return {offset: symbol for offset, symbol in found.items() if offset not in conflicts}


def _string_bytes(literal: str) -> int | None:
    inner = literal[1:-1]
    if any(ord(c) > 0x7E for c in inner):
        return None                                # charmap-converted text; byte length is not the character count
    return len(ESCAPE.sub("_", inner)) + 1


def named_rodata(source: str, function: str, diff: str):
    """String literals the target reads through a named rodata symbol: `"%6dG"` -> `gShopMenuMoneyFormat`.

    2026-09-14 90+ census: 13 functions differed by `%lo(.rodata)` against a named target
    label (6 with no other non-register fault). The literal form cannot certify (its TU
    placement failed the whole-ROM checksum for drawRaceSplitscreenSelectEntryFee), while the
    named extern certifies under function_boundary schema 3 and leaves the definition to the
    integration gate. Offsets follow IDO's literal pool: textual order, 4-byte alignment, which
    every observed site must agree with or the family declines.
    """
    names = rodata_names(diff)
    if not names:
        return
    match, end = repair_context.definition(source, function)
    from solver import project_headers
    masked = project_headers._mask_noncode(source[:match.end()] + source[match.end():end].replace('"', "\0"))
    body_start = match.end()
    body = source[body_start:end]
    cursor, layout = 0, {}
    for found in STRING.finditer(body):
        if masked[body_start + found.start()] != "\0":
            continue                                   # inside a comment
        length = _string_bytes(found.group(0))
        if length is None:
            return
        layout[cursor] = found
        cursor = (cursor + length + 3) & ~3
    if not set(names) <= set(layout):
        return
    text = body
    for offset in sorted(names, key=lambda o: layout[o].start(), reverse=True):
        literal = layout[offset]
        text = text[:literal.start()] + names[offset] + text[literal.end():]
    rewritten = source[:body_start] + text + source[end:]
    missing = sorted({s for s in names.values() if not re.search(rf"\b{re.escape(s)}\b", source[:body_start])})
    if missing:
        line_start = source.rfind("\n", 0, match.start()) + 1
        yield ("named_rodata:extern", "named_rodata",
               rewritten[:line_start] + "".join(f"extern char {s}[];\n" for s in missing) + rewritten[line_start:])
    yield ("named_rodata", "named_rodata", rewritten)


def variants(source: str, function: str, diff: str, segments: dict | None = None):
    yield from literal_addresses(source, function, diff, segments)
    try:
        yield from named_rodata(source, function, diff)
    except ValueError:
        return


def literal_addresses(source: str, function: str, diff: str, segments: dict | None = None):
    """With `segments` (segments_from), spell addresses as the link-defined segment bounds.

    The splat target names them `D_593D10`, which the per-function oracle accepts but the
    real build does not define: initMainMenuSettings failed integration with an undefined
    reference (2026-09-14). `_593D10_ROM_START` is what include/assets.h and the linker use.
    """
    table = addresses(diff)
    if not table:
        return
    by_address = {}
    for symbol, address in table.items():
        by_address.setdefault(address, set()).add(symbol)
    by_address = {a: next(iter(s)) for a, s in by_address.items() if len(s) == 1}
    match, end = repair_context.definition(source, function)
    body_start = match.end()
    body = source[body_start:end]
    edits, used, previous, previous_end = [], [], None, -1
    for found in POINTER_CAST.finditer(body):
        address = int(found.group("lit"), 0)
        symbol = by_address.get(address)
        if symbol is None or _dereferenced(body, found.start()):
            previous = None
            continue
        adjacent = previous if previous is not None and re.fullmatch(r"\s*,\s*", body[previous_end:found.start()]) else None
        if segments:
            linked = _link_name(address, adjacent, segments)
            symbol = linked or symbol
            starting = segments.get(address, ([], []))[0]
            previous = (address, starting[0]) if linked and linked.endswith("_ROM_START") and len(starting) == 1 else None
        previous_end = found.end()
        edits.append((found.start("lit"), found.end(), "&" + symbol))
        if symbol not in used:
            used.append(symbol)
    if not edits:
        return
    if segments and any(s.endswith(("_ROM_START", "_ROM_END")) for s in used):
        text = body
        for start, stop, replacement in sorted(edits, reverse=True):
            text = text[:start] + replacement + text[stop:]
        rewritten = source[:body_start] + text + source[end:]
        if not re.search(r'#\s*include\s*"assets\.h"', source):
            line_start = source.rfind("\n", 0, match.start()) + 1
            rewritten = rewritten[:line_start] + '#include "assets.h"\n' + rewritten[line_start:]
        yield ("address_symbols:segments", "address_symbol", rewritten)
    text = body
    for start, stop, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[stop:]
    rewritten = source[:body_start] + text + source[end:]
    missing = [s for s in used if not re.search(rf"\b{re.escape(s)}\b", source[:body_start])]
    if missing:
        line_start = source.rfind("\n", 0, match.start()) + 1
        declared = (rewritten[:line_start] + "".join(f"extern u8 {s};\n" for s in missing) + rewritten[line_start:])
        yield ("address_symbols:extern", "address_symbol", declared)
    # A project header may already declare the symbol; then a local extern would conflict.
    yield ("address_symbols", "address_symbol", rewritten)
