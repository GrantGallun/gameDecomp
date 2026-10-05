"""m2c's unaligned word copies back into the struct assignment IDO compiled them from.

Confirmed rule (catalog `ido53-unaligned-struct-copy`, eval/results/unaligned-copy-20260929): IDO lowers an
assignment of an alignment-1 struct (all `u8`) of N bytes to word copies, loading the source with
`lwl/lwr` and, when the destination is a *declared local* of that struct type, storing with an aligned `sw`
(through a cast pointer or a union member the stores become `swl/swr` too). At 8 bytes it is two straight-line
word copies; at 40 it is a loop of three words per iteration ending at `src + N - 4`, then the rest.

m2c transcribes those copies and the pipeline lowers each unaligned word to four byte loads packed with
shifts, so the candidate has `lbu/sll/or` where the target has `lwl/lwr` (8 unsolved libultra PIF/controller
functions, 2026-09-29). Two source shapes are undone here, each into one assignment:

  loop     `p = (u8 *)SRC; q = ((u8 *)DST); end = p + K; for (;;) { ...; if (!(p != end)) break; }` + trailing
           word stores through q                                                   (osMotorStart, 40 bytes)
  straight `(*(s32 *)((u8 *)(&DST) + 0)) = PACKED(SRC[0..3]); ... + 4)) = PACKED(SRC[4..7]);`
                                                                                    (__osContGetInitData, 8 bytes)

A destination already declared with a struct type keeps it (`DST = *(T *)(SRC)`: whether T is alignment-1 is the
compiler's call); a destination declared as an array is retyped to `UnalignedN` and its other uses read through
its address with the original element type.
"""
from __future__ import annotations

import re

_IDENT = r"[A-Za-z_]\w*"
_NUM = r"-?(?:0x[0-9A-Fa-f]+|\d+)"
_PRIMS = {"u8", "s8", "u16", "s16", "u32", "s32", "int", "char", "short", "long", "unsigned", "signed", "f32", "f64",
          "float", "double"}


def _gate(diff: str) -> bool:
    from solver import diffrepair
    target, candidate = diffrepair._streams(diff or "")
    count = lambda s: sum(1 for x in s if x.split() and x.split()[0] in ("lwl", "lwr"))
    return count(target) > count(candidate)


def _packed(src: str, base: int) -> str:
    """m2c's lowering of one unaligned word at SRC[base..base+3]."""
    s = re.escape(src)
    parts = [rf"\(\(u32\)\(\(u8\s*\*\){s}\)\[{base + i}\](?:\s*<<\s*{sh})?\)" for i, sh in ((0, 24), (1, 16), (2, 8))]
    last = rf"\(\(u32\)\(\(u8\s*\*\){s}\)\[{base + 3}\]\)"
    return rf"(?:\(s32\)\s*)*\(\s*{parts[0]}\s*\|\s*{parts[1]}\s*\|\s*{parts[2]}\s*\|\s*{last}\s*\)"


def _retarget(body: str, dst: str, size: int):
    """(new_body, typedef or None, type name) with DST able to take `DST = *(T *)(...)`, or None."""
    decl = re.search(rf"^([ \t]*)((?:(?:unsigned|signed|struct|union|const)\s+)*{_IDENT})\s+{dst}\s*(\[\s*({_NUM})\s*\])?\s*;[^\n]*\n",
                     body, re.M)
    if not decl:
        return None
    indent, ctype, array = decl.group(1), decl.group(2), decl.group(3)
    if not array:
        if ctype.split()[-1] in _PRIMS:
            return None
        return body, None, ctype                       # already a struct: keep its type
    tname = f"Unaligned{size}"
    new = body[:decl.start()] + f"{indent}{tname} {dst};\n" + body[decl.end():]
    head, tail = new[:decl.start() + len(indent) + len(tname) + len(dst) + 3], new[decl.start() + len(indent) + len(tname) + len(dst) + 3:]
    # A constant byte index into the old array becomes a NAMED field. Catalog ido53-o1-array-element-base: at
    # -O1 an array element, even `l.bytes[38]`, is loaded off a computed base (`addiu t0,sp,40; lbu t1,38(t0)`),
    # which also cost osMotorStart an `andi s0` and a larger frame; a named field loads off sp directly
    # (`lbu t0,62(sp)`), as the target does. Hence `UnalignedN` has fields b0..bN-1, not an array.
    def field(m):
        k = int(m.group(1), 0)
        return f"{dst}.b{k}" if 0 <= k < size else m.group(0)
    tail = re.sub(rf"\(\s*\(\s*(?:u8|unsigned\s+char)\s*\*\s*\)\s*&?\s*{dst}\s*\)\s*\[\s*({_NUM})\s*\]", field, tail)
    tail = re.sub(rf"(?<![\w.&]){dst}\b(?!\s*=\s*\*\({tname})(?!\.b\d)", f"(({ctype} *)&{dst})", tail)
    fields = " ".join(f"u8 b{i};" for i in range(size))
    return head + tail, f"typedef struct {{ {fields} }} {tname};\n", tname


def _loops(body: str):
    head = re.compile(
        rf"^(?P<indent>[ \t]*)(?P<sp>{_IDENT})\s*=\s*\(u8\s*\*\)\s*(?P<src>[^;]+?);[ \t]*\n"
        rf"[ \t]*(?P<dp>{_IDENT})\s*=\s*\(?\(u8\s*\*\)\s*(?P<dst>{_IDENT})\)?;[ \t]*\n"
        rf"[ \t]*(?P<end>{_IDENT})\s*=\s*(?P=sp)\s*\+\s*(?P<k>{_NUM});[ \t]*\n"
        rf"[ \t]*for\s*\(\s*;\s*;\s*\)\s*\{{", re.M)
    for m in head.finditer(body):
        close = _matching_brace(body, m.end() - 1)
        if close is None:
            continue
        if not re.search(rf"if\s*\(\s*!\s*\(\s*{m['sp']}\s*!=\s*{m['end']}\s*\)\s*\)\s*break;", body[m.end():close]):
            continue
        tail = re.compile(rf"\s*\(\*\(s32\s*\*\)\s*\(\(u8\s*\*\)\s*\({m['dp']}\)\s*\+\s*{_NUM}\)\)\s*=[^;]*;")
        pos, words = close + 1, 0
        while (t := tail.match(body, pos)):
            pos, words = t.end(), words + 1
        if words:
            yield m.start(), pos, m["indent"], m["dst"], m["src"].strip(), int(m["k"], 0) + 4 * words


def _straight(body: str):
    store = re.compile(rf"^(?P<indent>[ \t]*)\(\*\(s32\s*\*\)\s*\(\(u8\s*\*\)\s*\(&(?P<dst>{_IDENT})\)\s*\+\s*(?P<off>{_NUM})\)\)\s*=\s*"
                       rf"(?:\(s32\)\s*)*\(\s*\(\(u32\)\(\(u8\s*\*\)(?P<src>{_IDENT})\)\[(?P<b>\d+)\]", re.M)
    for m in store.finditer(body):
        if int(m["off"], 0) != 0 or int(m["b"]) != 0:
            continue
        pos, words = m.start(), 0
        while True:
            line = re.compile(rf"[ \t]*\(\*\(s32\s*\*\)\s*\(\(u8\s*\*\)\s*\(&{m['dst']}\)\s*\+\s*{4 * words}\)\)\s*=\s*"
                              + _packed(m["src"], 4 * words) + r"\s*;[ \t]*\n?")
            t = line.match(body, pos)
            if not t:
                break
            pos, words = t.end(), words + 1
        if words >= 2:
            yield m.start(), pos, m["indent"], m["dst"], m["src"], 4 * words


def variants(source: str, function: str, diff: str):
    """(label, candidate) per unaligned copy in `function`; gated on the target having more lwl/lwr."""
    if not _gate(diff):
        return
    from solver import branch_shape
    try:
        begin, stop = branch_shape._body(source, function)
    except Exception:
        return
    body = source[begin:stop]
    for shape, finder in (("loop", _loops), ("straight", _straight)):
        for start, end, indent, dst, src, size in finder(body):
            retargeted = _retarget(body[:start] + "\0COPY\0" + body[end:], dst, size)
            if retargeted is None:
                continue
            new_body, typedef, tname = retargeted
            new_body = new_body.replace("\0COPY\0", f"{indent}{dst} = *({tname} *)({src});\n")
            new_body = _drop_orphans(new_body, body[start:end])
            prefix = source[:begin]
            if typedef and typedef not in prefix:
                includes = list(re.finditer(r"^#include[^\n]*\n", prefix, re.M))
                at = includes[-1].end() if includes else 0
                prefix = prefix[:at] + typedef + prefix[at:]
            yield f"unaligned_copy:{shape}:{dst}:{size}", prefix + new_body + source[stop:]


def _drop_orphans(body: str, removed: str) -> str:
    """Remove declarations of locals that only the replaced copy used (m2c's cursors and temporaries).

    At -O1 every declared local has a frame home, so osMotorStart kept four dead words after the rewrite
    and its frame grew 0x50 -> 0x70, shifting every stack offset. Only locals referenced inside the
    replaced span are candidates; other unused locals are the draft's own business.
    """
    for name in dict.fromkeys(re.findall(_IDENT, removed)):
        decl = re.search(rf"^[ \t]*[\w \t\*]+?\b{name}\s*;[^\n]*\n", body, re.M)
        if not decl or not re.match(rf"^[ \t]*(?:[\w]+[ \t\*]+)+{name}\s*;", decl.group(0)):
            continue
        rest = body[:decl.start()] + body[decl.end():]
        if not re.search(rf"\b{name}\b", rest):
            body = rest
    return body


def _matching_brace(text: str, open_at: int) -> int | None:
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return None
