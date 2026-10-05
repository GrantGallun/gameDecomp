"""m2c `void *` byte cursors that IDO rejects (2026-09-14 non-compiling census, second blocker).

After placeholder declarations are resolved, 24 of the 31 placeholder-blocked functions stop on
m2c's GNU-style `void *` usage, which IDO does not accept:

  gRegionAllocPtr = temp_v0 + 8;          Unacceptable operand of '+'   (arithmetic)
  var_v1 += 4;                            Bad operand type for += or -=
  temp_v0->unk4 = 0;                      'unk4' undefined / Selector requires struct/union pointer

m2c's offsets are byte offsets (GNU `void *` arithmetic), so spelling the same arithmetic through
`u8 *` keeps the address and the code generation. A pseudo-field `p->unkN` becomes a byte view
`(*(T *)((u8 *)p + 0xN))`, with T the width the target's own loads/stores use at displacement N
when those agree, and `s32` otherwise (a proposal; the object comparison decides).

The existing `void_field_repair` covers diagnostic-bound fields with a parameter-rooted alias
chain inside model-repair normalization; this pass is the draft-level lowering compile recovery
needs before any diagnostics exist.
"""
from __future__ import annotations

import re

from solver import project_headers, repair_context

LOAD_STORE = re.compile(r"^\s*(?P<op>lb|lbu|lh|lhu|lw|sb|sh|sw|lwc1|swc1|ldc1|sdc1)\s+[^,]+,\s*"
                        r"(?P<off>-?(?:0x[0-9a-fA-F]+|\d+))\((?P<base>\$?\w+)\)", re.M)
TYPES = {"lb": "s8", "lbu": "u8", "sb": "s8", "lh": "s16", "lhu": "u16", "sh": "s16", "lw": "s32", "sw": "s32",
         "lwc1": "f32", "swc1": "f32", "ldc1": "f64", "sdc1": "f64"}


SCALAR = r"(?:s8|u8|s16|u16|s32|u32|f32|f64|int|short|char|unsigned\s+char|unsigned\s+int|unsigned\s+short)"


def field_cursors(source: str, function: str) -> set[str]:
    """Pointers whose `->unkN` has no declared member: void pointers, scalar pointers, and pointers to a
    struct tag m2c reports as only forward-declared (`Warning: struct T is not defined`)."""
    match, end = repair_context.definition(source, function)
    masked = project_headers._mask_noncode(source)
    body = masked[match.end():end]
    names = void_pointers(source, function)
    incomplete = set(re.findall(r"struct\s+(\w+)\s+is not defined", source))
    declarations = re.findall(r"(?m)^[ \t]*(?:volatile\s+|const\s+)*(struct\s+\w+|" + SCALAR + r"|\w+)\s*\*\s*(\w+)\s*;", body)
    declarations += re.findall(r"(?:^|,)\s*(?:volatile\s+|const\s+)*(struct\s+\w+|" + SCALAR + r"|\w+)\s*\*\s*(\w+)\s*(?=,|$)",
                               match.group(2))
    for spelled, name in declarations:
        tag = re.fullmatch(r"struct\s+(\w+)", spelled)
        if re.fullmatch(SCALAR, spelled) or (tag and tag.group(1) in incomplete) or spelled in incomplete:
            names.add(name)
    return names


def void_pointers(source: str, function: str) -> set[str]:
    """Names declared exactly `void *name` as a body local, a parameter, or a file-scope extern in the draft."""
    match, end = repair_context.definition(source, function)
    masked = project_headers._mask_noncode(source)
    body = masked[match.end():end]
    names = set(re.findall(r"(?m)^[ \t]*void\s*\*\s*(\w+)\s*;", body))
    names |= {p.group(1) for p in re.finditer(r"^\s*void\s*\*\s*(\w+)\s*$", match.group(2).replace(",", "\n"), re.M)}
    names |= set(re.findall(r"(?m)^[ \t]*extern\s+void\s*\*\s*(\w+)\s*;", masked[:match.start()]))
    return names


def field_types(assembly: str) -> dict[int, str]:
    """Displacement -> element type, only where every non-stack load/store at that displacement agrees on width."""
    widths: dict[int, set[str]] = {}
    loads: dict[int, set[str]] = {}
    for found in LOAD_STORE.finditer(assembly):
        if found.group("base").lstrip("$") in ("sp", "fp", "s8"):
            continue
        offset, spelled = int(found.group("off"), 0), TYPES[found.group("op")]
        widths.setdefault(offset, set()).add(spelled.lstrip("su"))
        if found.group("op").startswith("l"):
            loads.setdefault(offset, set()).add(spelled)
    agreed = {}
    for offset, kinds in widths.items():
        if len(kinds) != 1:
            continue
        kind = next(iter(kinds))
        if kind in ("f32", "f64") or kind.startswith("f"):
            agreed[offset] = "f32" if "32" in kind else "f64"
            continue
        # Stores do not reveal signedness; unanimous loads do. Otherwise the signed spelling.
        read = loads.get(offset, set())
        agreed[offset] = next(iter(read)) if len(read) == 1 else "s" + kind
    return agreed


def propose(source: str, function: str, assembly: str = "") -> tuple[str, dict]:
    report = {"arithmetic": [], "fields": [], "compound": [], "declines": []}
    report["comparisons"], report["bitwise"] = [], []
    try:
        names = void_pointers(source, function)
        cursors = field_cursors(source, function)
        match, end = repair_context.definition(source, function)
    except ValueError as exc:
        report["declines"].append(str(exc))
        return source, report
    body = source[match.end():end]
    masked = project_headers._mask_noncode(source)[match.end():end]
    types = field_types(assembly) if assembly else {}
    edits = []
    # m2c's `(bitwise T)` reinterpretation spelling; for an integer T it is an ordinary cast.
    for found in re.finditer(rf"\(\s*bitwise\s+(?P<type>{SCALAR})\s*\)", masked):
        edits.append((found.start(), found.end(), f"({found.group('type')})"))
        report["bitwise"].append(found.group("type"))
    if cursors:
        fields = "|".join(sorted(map(re.escape, cursors), key=len, reverse=True))
        # `p->unkN` -> `(*(T *)((u8 *)p + 0xN))`
        for found in re.finditer(rf"(?<![\w.>])(?P<name>{fields})\s*->\s*unk(?P<hex>[0-9A-Fa-f]+)\b", masked):
            offset = int(found.group("hex"), 16)
            spelled = types.get(offset, "s32")
            edits.append((found.start(), found.end(), f"(*({spelled} *)((u8 *){found.group('name')} + 0x{offset:X}))"))
            report["fields"].append({"name": found.group("name"), "offset": offset, "type": spelled,
                                     "evidence": "target displacement width" if offset in types else "default word"})
    # `p != &X` between differently typed pointers (`void **p` against `&D_801121E0`, `s16 *p` against
    # `&sp266`): IDO wants one pointer type, so the address takes the local's own declared type.
    pointer_locals = {m.group("name"): m.group("type").strip() for m in re.finditer(
        r"(?m)^[ \t]*(?P<type>(?:(?:volatile|const|unsigned|signed|struct)\s+)*\w+\s*\*[\s*]*)(?P<name>\w+)\s*;", masked)}
    if pointer_locals:
        locals_ = "|".join(sorted(map(re.escape, pointer_locals), key=len, reverse=True))
        for found in re.finditer(rf"(?<![\w.>])(?P<name>{locals_})\s*[!=]=\s*(?P<other>&\s*[\w.\[\]]+)", masked):
            start = found.start("other")
            spelled = re.sub(r"\s+\*", " *", pointer_locals[found.group("name")]).replace("* *", "**")
            edits.append((start, start, f"({spelled})"))
            report["comparisons"].append(found.group("name"))
    if not names:
        return _apply(source, match, end, body, edits), report
    alternation = "|".join(sorted(map(re.escape, names), key=len, reverse=True))
    # `p += n;` / `p -= n;` -> `p = (void *)((u8 *)p + (n));`
    for found in re.finditer(rf"(?<![\w.>])(?P<name>{alternation})\s*(?P<op>[+-])=\s*(?P<rhs>[^;]+);", masked):
        rhs = body[found.start("rhs"):found.end("rhs")].strip()
        name = found.group("name")
        edits.append((found.start(), found.end(), f"{name} = (void *)((u8 *){name} {found.group('op')} ({rhs}));"))
        report["compound"].append(name)
    # Remaining arithmetic operands: `p + n`, `n + p`, `p - n` (never `p - q` of two cursors).
    covered = [(s, e) for s, e, _r in edits if e > s]
    for found in re.finditer(rf"(?<![\w.>])(?P<name>{alternation})\b", masked):
        if any(s <= found.start() < e for s, e in covered):
            continue
        after = masked[found.end():found.end() + 3].lstrip()
        before = masked[max(0, found.start() - 3):found.start()].rstrip()
        forward = after[:1] in "+-" and after[:2] not in ("++", "--", "+=", "-=", "->")
        backward = before.endswith("+") and not before.endswith("++")
        if not (forward or backward):
            continue
        if after[:1] == "-":
            other = re.match(r"\s*-\s*(\w+)", masked[found.end():])
            if other and other.group(1) in names:
                continue                                   # pointer difference keeps its meaning
        edits.append((found.start(), found.end(), f"((u8 *){found.group('name')})"))
        report["arithmetic"].append(found.group("name"))
    return _apply(source, match, end, body, edits), report


WORD_DEREF = re.compile(r"\(\s*\*\s*\(\s*(?:s32|u32|int|unsigned\s+int|void\s*\*)\s*\*\s*\)")


def word_calls(source: str, function: str) -> tuple[str, list[str]]:
    """`(*(s32 *)(p + 8))(a)` -> `(*(s32 (**)())(p + 8))(a)`: the same word load, called through its value.
    IDO rejects calling a non-function (`Non-function name referenced in function call`, 6 functions)."""
    match, end = repair_context.definition(source, function)
    masked = project_headers._mask_noncode(source)
    edits, found_calls = [], []
    for found in WORD_DEREF.finditer(masked, match.end(), end):
        depth, cursor = 0, found.start()
        while cursor < end:
            depth += (masked[cursor] == "(") - (masked[cursor] == ")")
            cursor += 1
            if depth == 0:
                break
        if depth or not masked[cursor:end].lstrip().startswith("("):
            continue
        cast = re.search(r"\(\s*(?:s32|u32|int|unsigned\s+int|void\s*\*)\s*\*\s*\)", masked[found.start():found.end()])
        edits.append((found.start() + cast.start(), found.start() + cast.end(), "(s32 (**)())"))
        found_calls.append(source[found.start():cursor][:60])
    for start, stop, replacement in sorted(edits, reverse=True):
        source = source[:start] + replacement + source[stop:]
    return source, found_calls


def source_signals(source: str) -> bool:
    """Name-free scheduling check for a stored draft (nodes do not carry the function name here)."""
    for name in re.findall(r"(?m)^[ \t]*void\s*\*\s*(\w+)\s*;", source):
        if re.search(rf"(?<![\w.>]){re.escape(name)}\s*(?:->\s*unk|[+-]=|[+-](?![+\-=>]))", source):
            return True
    if re.search(r"\(\s*bitwise\s+\w", source) or re.search(WORD_DEREF.pattern + r"[^;]*?\)\s*\)\s*\(", source):
        return True
    return re.search(r"struct\s+\w+\s+is not defined", source) is not None and "->unk" in source


def signals(source: str, function: str) -> bool:
    """Scheduling check: a draft-level `void *`/incomplete-pointer shape this pass lowers."""
    try:
        lowered, _report = propose(source, function)
        lowered, _calls = word_calls(lowered, function)
    except ValueError:
        return False
    return lowered != source


def lowered_candidates(source: str, function: str, headers: str = "", assembly: str = "") -> list[tuple[str, str]]:
    """Placeholder resolution (placeholder_declarations) then byte-unit lowering, most complete first."""
    from solver import placeholder_declarations
    seeds = placeholder_declarations.propose(source, function, headers)[0] or [("", source)]
    rows = []
    for fix, seed in seeds:
        lowered, _report = propose(seed, function, assembly)
        lowered, _calls = word_calls(lowered, function)
        if lowered != seed:
            rows.append(((fix + "+" if fix else "") + "void-units", lowered))
    rows += [(fix, seed) for fix, seed in seeds if fix]
    unique, seen = [], {source}
    for label, code in rows:
        if code not in seen:
            unique.append((label, code))
            seen.add(code)
    return unique


def _apply(source, match, end, body, edits):
    # Insertions (start == stop) sort after a replacement ending at the same point, so they land in front of it.
    for start, stop, replacement in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        body = body[:start] + replacement + body[stop:]
    return source[:match.end()] + body + source[end:]
