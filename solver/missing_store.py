"""Write the store the target performs and the candidate does not, from the target's own instructions.

A dropped statement leaves target-only rows in the oracle diff and NOTHING on the candidate side, so the
localizer has no line to name and every edit family that rewrites an existing line declines (measured on 6
planted dropped statements, eval/results/edit-capability-20261002: site_edits 0/6, the model 1/6 even with
the asm). The simplest such statement is a store whose value the target states outright:

    sh    zero,0x18(a0)                        -> arg0->unk18 = 0;
    li    t7,-0x38 ... sh t7,0x1a(a0)           -> arg0->unk1A = -0x38;
    lh    t7,0x30(a0); addiu t8,t7,-0x30; sh t8,0x30(a0)   -> arg0->unk30 -= 0x30;

The base register must be an incoming argument (directly, or copied to a saved register with `move sN,aK`).
Each statement is offered in two spellings -- the member the binary context already names `unk<OFF>`, and a
raw offset access that compiles to the same store -- at statement boundaries beside the source lines the
compiler charges the target-only rows to. Every variant is a hypothesis; the oracle decides. Deterministic,
LLM-free, and it reads only the binary's instructions and the candidate's own text.
"""
from __future__ import annotations

import re
from collections import Counter

STORES = {"sb": "s8", "sh": "s16", "sw": "s32"}
LOADS = {"lb", "lbu", "lh", "lhu", "lw"}
MEM = re.compile(r"^(\w+)\s+\$?(\w+),\s*(-?(?:0x[0-9a-fA-F]+|\d+))\(\$?(\w+)\)$")
CAP = 24


def _streams(diff: str) -> tuple[list[str], list[str]]:
    target, candidate = [], []
    for row in diff.splitlines():
        if not row or row.startswith(("---", "+++", "@@")):
            continue
        text = row[1:].strip()
        if not text:
            continue
        if row[0] in " -":
            target.append(text)
        if row[0] in " +":
            candidate.append(text)
    return target, candidate


def _mem(text: str):
    m = MEM.match(text)
    return (m.group(1), m.group(2), int(m.group(3), 0), m.group(4)) if m else None


def _arg_of(stream: list[str], upto: int, reg: str) -> int | None:
    """Incoming argument index held by `reg` at position `upto` (a0-a3, or a saved copy of one)."""
    if re.fullmatch(r"a[0-3]", reg):
        return int(reg[1])
    for text in reversed(stream[:upto]):
        m = re.match(rf"^move\s+\$?{reg},\s*\$?(a[0-3])$", text)
        if m:
            return int(m.group(1)[1])
    return None


def _value(stream: list[str], at: int, reg: str, offset: int, base: str):
    """('set', literal) or ('add', delta) for the register stored at `at`; None when not stated."""
    if reg == "zero":
        return ("set", 0)
    for i in range(at - 1, -1, -1):
        text = stream[i]
        op = text.split(None, 1)[0]
        operands = [x.strip().lstrip("$") for x in text.split(None, 1)[1].split(",")] if " " in text else []
        if not operands or operands[0] != reg:
            continue
        if op == "li":
            return ("set", int(operands[1], 0))
        if op == "move" and operands[1] == "zero":
            return ("set", 0)
        if op == "addiu" and len(operands) == 3:
            src, delta = operands[1], int(operands[2], 0)
            for j in range(i - 1, -1, -1):
                load = _mem(stream[j])
                if load and load[1] == src:
                    if load[0] in LOADS and load[2] == offset and load[3] == base:
                        return ("add", delta)
                    return None
            return None
        return None            # defined by something this module does not read
    return None


def missing(diff: str) -> list[dict]:
    """Target stores (op, offset) the candidate performs fewer times, with base argument and value."""
    target, candidate = _streams(diff)
    have = Counter((m[0], m[2]) for m in map(_mem, candidate) if m and m[0] in STORES and m[3] != "sp")
    seen: Counter = Counter()
    out = []
    for i, text in enumerate(target):
        m = _mem(text)
        if not m or m[0] not in STORES or m[3] == "sp":
            continue
        key = (m[0], m[2])
        seen[key] += 1
        if seen[key] <= have[key]:
            continue
        arg = _arg_of(target, i, m[3])
        value = _value(target, i, m[1], m[2], m[3])
        if arg is None or value is None:
            continue
        out.append({"op": m[0], "offset": m[2], "arg": arg, "value": value})
    return out


def _params(source: str, function: str) -> list[str] | None:
    m = re.search(rf"\b{re.escape(function)}\s*\(([^)]*)\)\s*\{{", source)
    if not m:
        return None
    names = []
    for part in m.group(1).split(","):
        ident = re.findall(r"[A-Za-z_]\w*", part)
        names.append(ident[-1] if ident else "")
    return names


def _literal(value: int) -> str:
    return f"-0x{-value:X}" if value < 0 else (f"0x{value:X}" if value > 9 else str(value))


def statements(source: str, function: str, store: dict) -> list[str]:
    params = _params(source, function)
    if not params or store["arg"] >= len(params) or not params[store["arg"]]:
        return []
    p, off, (kind, v) = params[store["arg"]], store["offset"], store["value"]
    rhs = (f" = {_literal(v)};" if kind == "set" else
           f" += {_literal(v)};" if v >= 0 else f" -= {_literal(-v)};")
    out = []
    member = f"unk{off:X}"
    if re.search(rf"\b{member}\b", source):
        out.append(f"{p}->{member}{rhs}")
    out.append(f"*({STORES[store['op']]} *)((u8 *){p} + 0x{off:X}){rhs}")
    return out


def _boundaries(source: str, function: str, lines: list[int]) -> list[tuple[int, str]]:
    """(insert-before line index 0-based, indentation) at statement boundaries beside `lines` (1-based)."""
    src = source.split("\n")
    m = re.search(rf"\b{re.escape(function)}\s*\([^)]*\)\s*\{{", source)
    if not m:
        return []
    body_first = source.count("\n", 0, m.end()) + 1           # 0-based index of the first body line
    depth, body_last = 0, None
    for i in range(body_first - 1, len(src)):
        depth += src[i].count("{") - src[i].count("}")
        if depth == 0 and i >= body_first - 1:
            body_last = i
            break
    if body_last is None:
        return []

    def opens_statement(i):        # inserting before src[i] keeps the C well formed
        prev = src[i - 1].rstrip()
        return body_first <= i <= body_last and prev.endswith((";", "{", "}")) \
            and not src[i].lstrip().startswith("else") and not _is_decl(src[i + 1:body_last], src[i])

    out = []
    for line in lines:
        for i in (line - 1, line):                            # before and after the attributed line
            if opens_statement(i):
                ref = src[i] if src[i].strip() and src[i].strip() != "}" else src[i - 1]
                indent = ref[:len(ref) - len(ref.lstrip())] or "    "
                if (i, indent) not in out:
                    out.append((i, indent))
    return out


DECL = re.compile(r"^\s*(?:register\s+|static\s+|const\s+|volatile\s+)*(?:struct\s+)?[A-Za-z_]\w*[\s*]+[A-Za-z_]\w*"
                  r"(?:\[[^\]]*\])?\s*(?:=[^;]*)?;\s*$")


def _is_decl(rest, line) -> bool:
    """C89: a statement may not precede a declaration, so never insert above one."""
    return bool(DECL.match(line)) and not line.lstrip().startswith("return")


def variants(source: str, function: str, diff: str, attribution: dict | None):
    """`(label, candidate)` per (missing store, spelling, position); nothing unless the diff has one."""
    stores = missing(diff)
    if not stores:
        return
    from solver import site_edits
    weights, _status = site_edits.site_lines(diff, attribution)
    lines = [line for line, _w in weights.most_common(3)]
    if not lines:
        return
    positions = _boundaries(source, function, lines)
    src = source.split("\n")
    count, seen = 0, {source}
    for store in stores:
        for text in statements(source, function, store):
            for at, indent in positions:
                child = "\n".join(src[:at] + [indent + text] + src[at:])
                if child in seen or count >= CAP:
                    continue
                seen.add(child)
                count += 1
                yield f"missing {store['op']} 0x{store['offset']:X}: {text} @L{at + 1}", child
