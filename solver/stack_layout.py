"""Stack-layout proposals for residuals that differ only in sp-relative offsets (2026-09-14 90+ census).

25 of 320 pending functions scoring >= 90 differed from the target ONLY in stack offsets or
frame size; 59 more carried stack differences beside other faults. The recurring causes:

  * an invented frame pad (`volatile u8 framePad[0x8];`, `int dummy;`) sized wrongly, so the
    frame is 8 bytes too large (osViBlack, osYieldThread, osSetThreadPri, loadRawRomAsset);
  * a stack local one alignment step away from its target slot (drawCourseSelectExtraCourseBadge:
    `sh a3,0x34(sp)` wanted, `0x36` produced);
  * locals declared in an order whose slots do not match (makeFixedRotationXZ family).

Measured with the project IDO recipe (eval/results/ninety-census-20260914/ido_stack_probe.py):
the first-declared local receives the highest address, later locals pack downward with type
alignment, unused declarations (volatile or not) still occupy their slots, and use order does
not matter. So declaration order, declared extents and unused pads are the source-level
controls, and every proposal here edits exactly one of them.

Evidence is the oracle diff: sizes for inserted/resized pads come from the observed frame and
slot deltas, never from a guessed layout. Proposals only; the object comparison decides.
"""
from __future__ import annotations

import re

from solver import project_headers, repair_context

HUNK_LINE = re.compile(r"^([-+ ])(\S+)\s*(.*)$")
NUMBER = r"-?(?:0x[0-9a-fA-F]+|\d+)"
SLOT = re.compile(rf"({NUMBER})\(sp\)")
DECLARATION = re.compile(
    r"^(?P<i>[ \t]*)(?P<qual>(?:(?:volatile|register|const|unsigned|signed|struct|union|enum)\s+)*)"
    r"(?P<type>[A-Za-z_]\w*)(?P<stars>\s*\*[\s*]*|\s+)(?P<name>[A-Za-z_]\w*)"
    r"(?P<array>(?:\s*\[\s*(?:0x[0-9a-fA-F]+|\d+)\s*\])*)(?P<init>\s*=[^;]*)?;[ \t]*(?:/\*.*?\*/|//.*)?$")
NOT_TYPES = {"return", "goto", "break", "continue", "else", "case", "default", "do", "if", "while", "for", "switch",
             "sizeof"}
ELEMENT = {"char": 1, "s8": 1, "u8": 1, "short": 2, "s16": 2, "u16": 2, "int": 4, "long": 4, "s32": 4, "u32": 4,
           "f32": 4, "float": 4, "f64": 8, "double": 8, "s64": 8, "u64": 8}
ALIGNED_PAD = {2: "s16 {name};", 4: "s32 {name};", 8: "s32 {name}[2];"}
# Bounded per family so a long declaration block cannot starve the other controls (64 compiles per round).
FAMILY_CAP = {"unused": 12, "extent": 8, "pad": 20, "order": 24}


def _pairs(diff: str):
    """Aligned (target, candidate) instruction pairs from each equal-length -/+ run of a unified diff."""
    out, minus, plus = [], [], []

    def flush():
        if len(minus) == len(plus):
            out.extend(zip(minus, plus))
        minus.clear()
        plus.clear()

    for line in diff.splitlines():
        if line.startswith(("---", "+++", "@@")):
            flush()
            continue
        found = HUNK_LINE.match(line)
        if not found:
            continue
        sign, mnemonic, operands = found.groups()
        if sign == "-":
            if plus:
                flush()
            minus.append((mnemonic, operands.replace(" ", "")))
        elif sign == "+":
            plus.append((mnemonic, operands.replace(" ", "")))
        else:
            flush()
    flush()
    return out


def stack_deltas(diff: str) -> dict:
    """Frame delta and sp-relative slot deltas (candidate minus target) that the diff states outright."""
    frame, slots = None, set()
    for (tm, to), (cm, co) in _pairs(diff):
        tops, cops = to.split(","), co.split(",")
        if tm != cm or len(tops) != len(cops):
            continue
        if tm == "addiu" and tops[:2] == ["sp", "sp"] and cops[:2] == ["sp", "sp"]:
            t, c = int(tops[2], 0), int(cops[2], 0)
            if t < 0 and c < 0 and t != c:
                frame = t - c                      # candidate frame size minus target frame size
            continue
        for index, (a, b) in enumerate(zip(tops, cops)):
            if a == b:
                continue
            ta, cb = SLOT.fullmatch(a), SLOT.fullmatch(b)
            if ta and cb:
                slots.add(int(cb.group(1), 0) - int(ta.group(1), 0))
            elif tm == "addiu" and index == 2 and tops[1] == "sp" and cops[1] == "sp":
                slots.add(int(b, 0) - int(a, 0))
    return {"frame": frame, "slots": sorted(slots)}


def _declarations(source: str, function: str):
    """Leading declaration lines of the function body as (start, stop, match), absolute offsets."""
    match, end = repair_context.definition(source, function)
    masked = project_headers._mask_noncode(source)
    found, offset = [], match.end()
    for line in source[match.end():end - 1].split("\n"):
        start, offset = offset, offset + len(line) + 1
        if not masked[start:start + len(line)].strip():
            continue                               # blank or comment-only
        decl = DECLARATION.match(line)
        if not decl or decl.group("type") in NOT_TYPES or decl.group("name") in NOT_TYPES:
            break
        found.append((start, min(offset, end - 1), decl))
    return found, match.end(), end


def _uses(text: str, name: str) -> int:
    return len(re.findall(rf"(?<![\w.>$])\b{re.escape(name)}\b", text))


def _count(decl) -> int:
    count = 1
    for size in re.findall(r"\[\s*(0x[0-9a-fA-F]+|\d+)\s*\]", decl.group("array") or ""):
        count *= int(size, 0)
    return count


def _element(decl) -> int | None:
    if "*" in decl.group("stars"):
        return 4
    return ELEMENT.get(decl.group("type"))


def _decay(source: str, function: str, names) -> str:
    """`&spXX` -> `spXX` for locals now declared as arrays: the same address, and the frontend
    policy rejects `s16 (*)[16]` where `s16 *` is expected (-Werror=incompatible-pointer-types)."""
    match, end = repair_context.definition(source, function)
    body = source[match.end():end]
    for name in names:
        body = re.sub(rf"&\s*{re.escape(name)}\b(?!\s*\[)", name, body)
    return source[:match.end()] + body + source[end:]


def _fresh_name(source: str, stem: str = "pad") -> str:
    index = 0
    while re.search(rf"\b{stem}{index or ''}\b", source):
        index += 1
    return f"{stem}{index or ''}"


def variants(source: str, function: str, diff: str):
    """Yield (label, kind, source) stack-layout proposals; nothing without an sp-relative difference."""
    deltas = stack_deltas(diff)
    if deltas["frame"] is None and not deltas["slots"]:
        return
    try:
        declarations, body_start, body_end = _declarations(source, function)
    except ValueError:
        return
    body = source[body_start:body_end]
    frame = abs(deltas["frame"] or 0)
    observed = {abs(d) for d in deltas["slots"]} | {frame}
    observed |= {abs(abs(d) - frame) for d in deltas["slots"]}
    sizes = sorted(s for s in observed if 0 < s <= 64)[:4]
    seen, emitted = {source}, {}

    def emit(label, kind, text):
        family = {"stack_decl_order": "order", "stack_insert_pad": "pad", "stack_named_extent": "extent"}.get(kind, "unused")
        if text in seen or emitted.get(family, 0) >= FAMILY_CAP[family]:
            return None
        seen.add(text)
        emitted[family] = emitted.get(family, 0) + 1
        return label, kind, text

    def calls(decl):
        return "(" in (decl.group("init") or "")

    # 1. Unused declarations: an invented, wrongly sized frame pad is the most common single cause.
    for start, stop, decl in declarations:
        name = decl.group("name")
        if _uses(body, name) > _uses(source[start:stop], name) or calls(decl):
            continue
        item = emit(f"stack_drop_unused:{name}", "stack_drop_unused", source[:start] + source[stop:])
        if item:
            yield item
        element = _element(decl)
        if element is None or decl.group("init"):
            continue
        extent = element * _count(decl)
        for size in sizes:
            for new in (extent - size, extent + size):
                if new <= 0 or new % element:
                    continue
                stars = decl.group("stars")
                line = (f"{decl.group('i')}{decl.group('qual')}{decl.group('type')}{stars}{name}"
                        f"[{new // element}];\n")
                item = emit(f"stack_resize_unused:{name}:{extent}->{new}", "stack_resize_unused",
                            source[:start] + line + source[stop:])
                if item:
                    yield item
    # 2. Pads sized by the observed deltas, before each declaration and after the last.
    indent = declarations[0][2].group("i") if declarations else "    "
    anchors = [s for s, _e, _d in declarations] + [declarations[-1][1] if declarations else body_start]
    name = _fresh_name(source)
    for size in sizes:
        spellings = [f"char {name}[{size}];"] + ([ALIGNED_PAD[size].format(name=name)] if size in ALIGNED_PAD else [])
        for spelling in spellings:
            for position, anchor in enumerate(anchors):
                pad = ("\n" if anchor == body_start else "") + f"{indent}{spelling}\n"
                item = emit(f"stack_insert_pad:{spelling}@{position}", "stack_insert_pad",
                            source[:anchor] + pad + source[anchor:])
                if item:
                    yield item
    # 3. Extents of m2c stack locals: `spXX` names the TARGET slot, so the next named slot above
    #    bounds how far an address-taken local may extend (makeFixedRotationXZ: `s16 sp38;`
    #    passed to a 3x3 matrix routine, next slot 0x20 away).
    named = sorted(((int(d.group("name")[2:], 16), s, e, d) for s, e, d in declarations
                    if re.fullmatch(r"sp[0-9A-Fa-f]+", d.group("name")) and not d.group("init")), key=lambda t: t[0])
    for index, (slot, start, stop, decl) in enumerate(named):
        element = _element(decl)
        if element is None or index + 1 >= len(named) or not re.search(rf"&\s*{decl.group('name')}\b", body):
            continue
        gap = named[index + 1][0] - slot
        counts = {gap // element} | {(gap - size) // element for size in sizes if gap > size}
        for count in sorted(c for c in counts if c > 1 and c != _count(decl)):
            line = f"{decl.group('i')}{decl.group('qual')}{decl.group('type')}{decl.group('stars')}{decl.group('name')}[{count}];\n"
            text = _decay(source[:start] + line + source[stop:], function, [decl.group("name")])
            item = emit(f"stack_named_extent:{decl.group('name')}[{count}]", "stack_named_extent", text)
            if item:
                yield item
    # 3b. The whole named layout at once: unused pads dropped and every address-taken spXX local
    #     sized by its gap (the topmost takes the widest gap). Greedy single edits cannot reach
    #     this when each partial step scores worse than the invented pad it replaces.
    taken = [(slot, s, e, d) for slot, s, e, d in named if _element(d) and re.search(rf"&\s*{d.group('name')}\b", body)]
    if len(taken) >= 2:
        gaps = [taken[k + 1][0] - taken[k][0] for k in range(len(taken) - 1)]
        edits = {s: (e, "") for s, e, d in declarations
                 if _uses(body, d.group("name")) <= _uses(source[s:e], d.group("name")) and not calls(d)}
        for k, (slot, start, stop, decl) in enumerate(taken):
            gap = gaps[k] if k < len(gaps) else max(gaps)
            count = gap // _element(decl)
            edits[start] = (stop, f"{decl.group('i')}{decl.group('qual')}{decl.group('type')}{decl.group('stars')}"
                                  f"{decl.group('name')}[{count}];\n")
        text = source
        for start in sorted(edits, reverse=True):
            stop, replacement = edits[start]
            text = text[:start] + replacement + text[stop:]
        text = _decay(text, function, [d.group("name") for _slot, _s, _e, d in taken])
        item = emit("stack_named_layout", "stack_named_extent", text)
        if item:
            yield item
    # 4. Declaration order: slots follow declaration order, not use order.
    if 1 < len(declarations) <= 12 and not any(calls(d) for _s, _e, d in declarations):
        first, last = declarations[0][0], declarations[-1][1]
        # Each chunk runs to the next declaration, so blank/comment lines travel with the one above.
        bounds = [s for s, _e, _d in declarations] + [last]
        lines = [source[bounds[k]:bounds[k + 1]] for k in range(len(declarations))]
        if all(line.endswith("\n") for line in lines):
            names = [d.group("name") for _s, _e, d in declarations]
            orders = [("stack_reverse_decls", list(reversed(range(len(lines)))))]
            for i in range(len(lines)):
                rest = [k for k in range(len(lines)) if k != i]
                orders.append((f"stack_hoist_decl:{names[i]}", [i] + rest))
                orders.append((f"stack_sink_decl:{names[i]}", rest + [i]))
            for i in range(len(lines)):
                for j in range(i + 1, len(lines)):
                    order = list(range(len(lines)))
                    order[i], order[j] = order[j], order[i]
                    orders.append((f"stack_swap_decl:{names[i]}<->{names[j]}", order))
            for label, order in orders:
                item = emit(label, "stack_decl_order", source[:first] + "".join(lines[k] for k in order) + source[last:])
                if item:
                    yield item


def signals(residual: dict) -> bool:
    """Cheap scheduling check on a stored residual; the diff at job time still decides."""
    faults = {k: v for k, v in (residual.get("faults") or {}).items() if v}
    first = " ".join(residual.get("first_difference") or [])
    return bool(faults) and sum(faults.values()) <= 40 and (
        bool({"immediate", "offset", "layout"} & set(faults)) or "(sp)" in first or "sp,sp" in first)
