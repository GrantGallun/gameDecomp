"""Localized typed edits: one general operator set instead of one generator per diff shape.

Measured 2026-09-29 on the live campaign: 60 of the 77 unsolved functions under 256 bytes whose
residual is at most six diff lines received ZERO proposals from the whole `rewrites.propose` pool,
yet hand-applying the single edit each diff pointed at certified 4 of 6 byte-exact (a constant split
over lui/ori, a store's signedness, a store's width through a cast, an array stride). Every one was
one token or one type at one expression. The shape-specific generators each decline on the next
shape over, which is why that pool yields ~1% per work item while `regalloc_search` -- a small general
mutation space searched against the oracle -- yields ~21%.

So this module does not recognise residual shapes. It asks two questions and lets the compiler answer:

  WHERE  -- which candidate source lines produced the mismatching instructions. Taken from IDO's own
            line records (`source_attribution`, verified against the final object), never guessed
            from syntax. Target-only instructions are charged to the candidate lines on either side
            of them in the same hunk.
  WHAT   -- a closed vocabulary of typed edits applied at those lines:
              literal    value + (target constant - candidate constant), for every constant pair the
                         aligned diff exposes, including 32-bit values split over lui+ori/addiu; also +-1
              type       swap an integer type token for another width/signedness
              decl       retype the declaration of an identifier used at the site (global, local,
                         parameter or struct member), when it is declared exactly once
              scale      multiply a subscript by 2**(target shift - candidate shift) from sll pairs
              uncast     drop a scalar cast

Every edit is a hypothesis; `search` hands each to the oracle and only `workspace.repair_complete`
(byte certificate plus frontend) ends it. Deterministic and LLM-free.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from solver import c89, code_shapes, diffrepair, signals

INT_TYPES = ("s8", "u8", "s16", "u16", "s32", "u32")
SPELLED = {
    "signed char": "s8", "unsigned char": "u8", "char": "s8",
    "unsigned short": "u16", "short": "s16",
    "unsigned int": "u32", "unsigned long": "u32", "unsigned": "u32", "int": "s32", "long": "s32",
    **{t: t for t in INT_TYPES},
}
TYPE_TOKEN = re.compile(r"\b(unsigned\s+(?:char|short|int|long)|signed\s+char|unsigned|char|short|int|long|"
                        r"[su](?:8|16|32))\b(?!\s*\()")
LITERAL = re.compile(r"(?<![\w.])(-?)(0[xX][0-9a-fA-F]+|\d+)([uUlL]*)(?![\w.])")
IDENT = re.compile(r"\b[A-Za-z_]\w*\b")
SCALAR_CAST = re.compile(r"\(\s*(?:unsigned\s+|signed\s+)?(?:char|short|int|long|[su](?:8|16|32))\s*\)")
RELATIONAL = re.compile(r"(?:(?<![<-])<=?|(?<![>-])>=?)\s*$|^(?:<(?![<])=?|>(?![>])=?)")
OPERAND = re.compile(r"^([a-z][a-z0-9.]*)\s*(.*)$")
KEYWORDS = {"if", "else", "while", "for", "do", "return", "switch", "case", "sizeof", "void",
            "struct", "union", "goto", "break", "continue", "default", "static", "extern"}


@dataclass(frozen=True)
class Edit:
    kind: str       # literal | type | decl | scale | uncast
    label: str
    line: int       # 1-based source line the edit touches
    start: int      # absolute character span replaced
    stop: int
    text: str
    also: tuple = ()  # further (start, stop, text) spans applied together with this one

    def apply(self, source: str) -> str:
        for start, stop, text in sorted(((self.start, self.stop, self.text),) + self.also, reverse=True):
            source = source[:start] + text + source[stop:]
        return source


# ---------------------------------------------------------------------------- WHERE

def site_lines(diff: str, attribution: dict | None, only=None) -> tuple[Counter, str]:
    """Candidate source line -> number of mismatching instructions charged to it, plus a status.

    Listing positions follow `residual_sites.mismatches` exactly, so the same verified line records
    (`normalized_line` -> `candidate_line`) are joined the same way. `only(instruction) -> bool`, when
    given, counts just the mismatching rows of one fault class (see solver.residual_classes.focus).
    """
    from solver.source_attribution import instructions_of
    if not attribution or attribution.get("status") != "verified":
        return Counter(), f"attribution {((attribution or {}).get('status') or 'missing')}"
    line_of = {r["normalized_line"]: r.get("candidate_line") for r in instructions_of(attribution)}
    weights: Counter = Counter()
    listing = None
    pending_target = 0            # target-only rows awaiting the next candidate position
    previous = None               # last candidate listing position seen in this hunk
    for row in diff.splitlines():
        hunk = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", row)
        if hunk:
            listing, pending_target, previous = int(hunk[1]), 0, None
            continue
        if listing is None or row.startswith(("---", "+++")) or not row:
            continue
        kind = row[0]
        counted = only is None or kind == " " or only(row[1:].strip())
        if kind == "-":
            if not counted:
                continue
            pending_target += 1
            if previous is not None and line_of.get(previous):
                weights[line_of[previous]] += 1
            continue
        if kind not in " +":
            continue
        here = line_of.get(listing)
        if here:
            if kind == "+" and counted:
                weights[here] += 1
            if pending_target:
                weights[here] += pending_target
        pending_target = 0
        previous = listing
        listing += 1
    return weights, "verified" if weights else "no mismatching instruction mapped to a source line"


# ---------------------------------------------------------------------------- WHAT: derived quantities

def _int(text: str) -> int | None:
    try:
        return int(text, 0)
    except ValueError:
        return None


def _s32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - (1 << 32) if value & 0x80000000 else value


def _constants(stream: list[str]) -> Counter:
    """Every constant a stream materialises: single immediates and lui+ori/addiu 32-bit pairs."""
    found: Counter = Counter()
    for index, instr in enumerate(stream):
        m = OPERAND.match(instr)
        if not m or "%" in instr:
            continue
        op, args = m.group(1), [a.strip() for a in m.group(2).split(",")]
        if op == "lui" and len(args) == 2 and _int(args[1]) is not None:
            high, reg = _int(args[1]) << 16, args[0]
            joined = False
            for follow in stream[index + 1:index + 4]:
                fm = OPERAND.match(follow)
                if not fm:
                    continue
                fargs = [a.strip() for a in fm.group(2).split(",")]
                if fm.group(1) in ("ori", "addiu") and len(fargs) == 3 and fargs[1] == reg \
                        and _int(fargs[2]) is not None:
                    low = _int(fargs[2])
                    value = high | (low & 0xFFFF) if fm.group(1) == "ori" else high + low
                    found[_s32(value)] += 1
                    joined = True
                    break
            if not joined:
                found[_s32(high)] += 1
        elif op in ("li", "addiu", "ori", "andi", "xori", "slti", "sltiu") and args \
                and _int(args[-1]) is not None:
            found[_s32(_int(args[-1]))] += 1
    return found


def deltas(diff: str) -> tuple[set[int], set[int], set[int], set[int]]:
    """(literal deltas, wanted target constants, subscript scale factors, target byte strides)."""
    target, cand = diffrepair._streams(diff)
    want, have = _constants(target), _constants(cand)
    wanted = {v for v in want if want[v] > have[v]}
    unwanted = {v for v in have if have[v] > want[v]}
    moves = {_s32(t - c) for t in wanted for c in unwanted if t != c}
    factors: set[int] = set()
    strides: set[int] = set()
    for t, c in signals._pairs(diff)[0]:
        mt, mc = OPERAND.match(t), OPERAND.match(c)
        if not (mt and mc) or mt.group(1) != mc.group(1) or mt.group(1) != "sll":
            continue
        at, ac = mt.group(2).split(","), mc.group(2).split(",")
        st, sc = _int(at[-1].strip()), _int(ac[-1].strip())
        if st is not None and sc is not None and st > sc:
            factors.add(1 << (st - sc))
            strides.add(1 << st)
    return moves, wanted, factors, strides


# ---------------------------------------------------------------------------- WHAT: edits

def _line_spans(source: str) -> list[tuple[int, int]]:
    spans, offset = [], 0
    for line in source.splitlines(keepends=True):
        spans.append((offset, offset + len(line)))
        offset += len(line)
    return spans


def _format(value: int, like: str) -> str:
    return (f"-0x{-value:X}" if value < 0 else f"0x{value:X}") if like.lower().startswith(("0x", "-0x")) \
        else str(value)


def _literal_edits(source, masked, number, start, stop, moves, wanted):
    out = []
    for m in LITERAL.finditer(masked, start, stop):
        sign, digits, suffix = m.group(1), m.group(2), m.group(3)
        value = int(digits, 0) * (-1 if sign else 1)
        options = {value + d for d in moves}
        # Off-by-one is a comparison-boundary phenomenon (`x < N` vs `x <= N-1`); elsewhere +-1 only
        # burns compiles on unrelated constants such as sound ids.
        if RELATIONAL.search(masked[max(start, m.start() - 4):m.start()]) or \
                RELATIONAL.match(masked[m.end():m.end() + 4].lstrip()):
            options |= {value + 1, value - 1}
        # A literal the compiler folded into a different constant cannot be reached by +-d alone when
        # the source spells it unsigned; propose the wanted constants themselves as well.
        options |= {w for w in wanted if abs(w) > 0xFF and (abs(w - value) < 0x100000)}
        for new in sorted(options - {value}, key=lambda v: abs(v - value)):
            text = _format(new, sign + digits) + suffix
            out.append(Edit("literal", f"{sign}{digits}{suffix} -> {text}", number, m.start(), m.end(), text))
    return out


def _canonical(token: str) -> str:
    return SPELLED.get(re.sub(r"\s+", " ", token), token)


def _type_edits(source, masked, number, start, stop, kind="type", label=""):
    out = []
    for m in TYPE_TOKEN.finditer(masked, start, stop):
        current = _canonical(m.group(1))
        for new in INT_TYPES:
            if new != current:
                out.append(Edit(kind, f"{label}{m.group(1)} -> {new}", number, m.start(), m.end(), new))
    return out


def _declaration_edits(source, masked, function, number, start, stop, region):
    """Retype the single declaration of each identifier used at the site."""
    out = []
    seen = set()
    for m in IDENT.finditer(masked, start, stop):
        name = m.group(0)
        if name in seen or name in KEYWORDS or name == function or name in SPELLED:
            continue
        seen.add(name)
        # `TYPE [*]name` followed by ; , [ ) or = -- declarations, members and parameters.
        rx = re.compile(r"\b((?:unsigned\s+|signed\s+)?(?:char|short|int|long|[su](?:8|16|32)))\s+\**\s*"
                        + re.escape(name) + r"\s*(?=[;,\[\)=])")
        hits = [d for d in rx.finditer(masked)
                if not (start <= d.start() < stop)]
        if len(hits) != 1:
            continue        # absent or ambiguous: do not guess which one feeds this site
        d = hits[0]
        decl_line = masked.count("\n", 0, d.start()) + 1
        current = _canonical(d.group(1))
        for new in INT_TYPES:
            if new != current:
                out.append(Edit("decl", f"{name}: {d.group(1)} -> {new}", decl_line,
                                d.start(1), d.end(1), new))
    return out


def _balanced_subscripts(masked, start, stop):
    for open_at in (i for i in range(start, stop) if masked[i] == "["):
        depth = 0
        for close in range(open_at, len(masked)):
            if masked[close] == "[":
                depth += 1
            elif masked[close] == "]":
                depth -= 1
                if depth == 0:
                    yield open_at + 1, close
                    break


def _scale_edits(source, masked, number, start, stop, factors):
    out = []
    for inner_start, inner_stop in _balanced_subscripts(masked, start, stop):
        expr = source[inner_start:inner_stop]
        for factor in sorted(factors):
            text = f"({expr}) * {factor}"
            out.append(Edit("scale", f"[{expr.strip()}] * {factor}", number, inner_start, inner_stop, text))
    return out


def _uncast_edits(source, masked, number, start, stop):
    return [Edit("uncast", f"drop {source[m.start():m.end()]}", number, m.start(), m.end(), "")
            for m in SCALAR_CAST.finditer(masked, start, stop)]


# Opt-in operator family (`propose(..., operators=True)`). Measured on planted single-line edits
# (eval/results/edit-capability-20261002/RESULT.md): `search` without it fixed 2 of 6 wrong-operator
# faults, and only where an equivalent literal edit existed (`< 0x21` == `<= 0x20`); with it, 6 of 6.
# The model fixed at most 1 of 6 even when told the line. Argument and adjacent-statement swaps were
# also built and dropped: the pool (`swap args i/j`, `move statement`) and the mined rule
# `( N0 , N1 ) -> ( N1 , N0 )` already fixed all 12 planted cases.
OPERATOR_RUN = re.compile(r"[-+<>=!&|*/%^~]+")
OPERATOR_SWAPS = {"+": ("-",), "-": ("+",), "<": ("<=", ">"), "<=": ("<", ">="), ">": (">=", "<"),
                  ">=": (">", "<="), "==": ("!=",), "!=": ("==",), "&": ("|",), "|": ("&",)}


def _operator_edits(source, masked, number, start, stop):
    """Flip one binary operator. Unary minus, address-of, `->`, `<<`, `&&` and compound assignment
    are excluded: the operator run must be exactly a table entry with an operand on both sides."""
    out = []
    for m in OPERATOR_RUN.finditer(masked, start, stop):
        swaps = OPERATOR_SWAPS.get(m.group(0))
        if not swaps:
            continue
        before = masked[start:m.start()].rstrip()
        after = masked[m.end():stop].lstrip()
        if not before or not (before[-1].isalnum() or before[-1] in "_)]") or \
                not after or not (after[0].isalnum() or after[0] in "_("):
            continue
        for new in swaps:
            out.append(Edit("operator", f"{m.group(0)} -> {new}", number, m.start(), m.end(), new))
    return out


DECLARED = re.compile(r"\b[A-Za-z_]\w*[\s*]+([A-Za-z_]\w*)\s*(?=[;=,\[\)])")
ASSIGN_FROM_LOCAL = re.compile(r"(?m)^([ \t]*)([^;{}=\n]*?[^\s=!<>+\-*/%&|^])\s*=\s*([A-Za-z_]\w*)\s*;")


def _locals(masked: str, region) -> set[str]:
    """Names declared in the function's parameters or body (over-approximate; only used to exclude)."""
    begin, end = region[1], region[2]
    head = masked.rfind("(", 0, masked.rfind(")", 0, begin)) if begin else 0
    return {m.group(1) for m in DECLARED.finditer(masked, max(0, head), end)}


def _readback_edits(source, masked, number, start, stop, region):
    """`G = t; ... use(t)` -> `use(G)`: re-read the stored non-local instead of the temporary.

    Probed 2026-09-29 (IDO 5.3 -O2, standalone file, patterns/catalog.py
    `store-then-reread-global-keeps-its-address`): after `gT = t;` a later read of `gT` is satisfied by
    store forwarding (the value stays in v0), but `&gT` has already been materialised into a register,
    so the store is emitted `lui r / addiu r / sw v0,0(r)` instead of `lui at / sw v0,%lo(gT)(at)`.
    Offered for the first later use and for all of them.
    """
    out = []
    locals_ = _locals(masked, region)
    for m in ASSIGN_FROM_LOCAL.finditer(masked, start, stop):
        lhs, temp = source[m.start(2):m.end(2)].strip(), m.group(3)
        base = IDENT.findall(masked[m.start(2):m.end(2)])
        if temp not in locals_ or not base or all(b in locals_ or b in KEYWORDS or b in SPELLED for b in base):
            continue        # only a store into something that is not a local
        uses = list(re.finditer(r"\b" + re.escape(temp) + r"\b", masked[m.end():region[2]]))
        if not uses:
            continue
        spans = [(m.end() + u.start(), m.end() + u.end(), f"({lhs})") for u in uses]
        first = spans[0]
        out.append(Edit("readback", f"re-read {lhs} for first use of {temp}", number, *first))
        if len(spans) > 1:
            out.append(Edit("readback", f"re-read {lhs} for all {len(spans)} uses of {temp}", number,
                            *first, tuple(spans[1:])))
    return out


STORE_FROM_LOCAL = re.compile(r"(?:(?<=[;{}])|(?m:^))[ \t]*([^;{}=\n]*?[^\s=!<>+\-*/%&|^])\s*=\s*"
                              r"([A-Za-z_]\w*)\s*;")
CALLISH = re.compile(r"\b(?!sizeof\b|if\b|while\b|for\b|switch\b|return\b)[A-Za-z_]\w*\s*\(")


def _copy_direction_edits(source, masked, number, start, stop, region):
    """`t = e; ... G = t;` -> `... G = e; t = G;`: copy the temporary FROM the store, not into it.

    Probed 2026-09-29 (IDO 5.3 -O2, standalone, patterns/catalog.py
    `copy-from-stored-global-keeps-address-in-a0`): `t = call(); gT = t;` folds the store to
    `sw v0,%lo(gT)(at)`, while `gT = call(); t = gT;` (and `t = gT = call();`) emits
    `lui a0 / addiu a0 / sw v0,0(a0)` with the fields still stored through v0 and no extra ugen
    temporary. Closed 5 of the 7 spawnEndingCredits* functions byte-exact with a one-statement rewrite.
    The move is offered only when nothing between the two statements calls, reads t, or writes what
    `e` reads.
    """
    out = []
    locals_ = _locals(masked, region)
    for m in STORE_FROM_LOCAL.finditer(masked, start, stop):
        lhs, temp = source[m.start(1):m.end(1)].strip(), m.group(2)
        base = IDENT.findall(masked[m.start(1):m.end(1)])
        if temp not in locals_ or not base or temp in base:
            continue
        prior = None
        for p in re.finditer(r"\b" + re.escape(temp) + r"\s*=\s*(?!=)([^;]+);", masked[region[1]:m.start()]):
            prior = p
        if prior is None:
            continue
        a, b = region[1] + prior.start(), region[1] + prior.end()
        between = masked[b:m.start()]
        expr = source[region[1] + prior.start(1):region[1] + prior.end(1)].strip()
        if re.search(r"\b" + re.escape(temp) + r"\b", between) or CALLISH.search(between):
            continue
        if any(re.search(r"\b" + re.escape(n) + r"\s*=(?!=)", between) for n in IDENT.findall(expr)):
            continue
        # See through a pointer local that only holds the global's address: `p = &G; *p = t;` is an
        # indirect store to uopt, which does not keep &G in a register (measured on
        # spawnEndingCreditsCharacterLoopingSparkle: unchanged gradient), so write G itself.
        through = re.fullmatch(r"\*\s*\(?\s*([A-Za-z_]\w*)\s*\)?", lhs)
        if through:
            held = re.search(r"\b" + re.escape(through.group(1)) + r"\s*=\s*&\s*([A-Za-z_]\w*)\s*;", between)
            if held:
                lhs = held.group(1)
        text = f"{lhs} = {expr};\n    {temp} = {lhs};"
        out.append(Edit("copydir", f"{temp} = e; {lhs} = {temp} -> {lhs} = e; {temp} = {lhs}", number,
                        m.start(1), m.end(), text, ((a, b, ""),)))
    return out


LOCAL_DECL = re.compile(r"(?m)^[ \t]*(?!return\b)[A-Za-z_][\w \t]*[\s*]\**[ \t]*[A-Za-z_]\w*(?:\[[^\]\n]*\])?[ \t]*;[ \t]*\n")
SP_SLOT = re.compile(r"^([a-z][a-z0-9]*)\s+(\w+),(-?(?:0x)?[0-9a-f]+)\(sp\)$")


def stack_slot_faults(diff: str) -> int:
    """Aligned pairs that differ only in an sp-relative offset: the local sits in another frame slot."""
    count = 0
    for t, c in signals._pairs(diff)[0]:
        mt, mc = SP_SLOT.match(t), SP_SLOT.match(c)
        if mt and mc and mt.group(1) == mc.group(1) and mt.group(3) != mc.group(3):
            count += 1
    return count


def _declaration_order_edits(source, masked, region, diff):
    """Swap adjacent local declarations when locals sit in the wrong frame slots.

    IDO assigns frame slots to locals by declaration order. Measured 2026-09-29 on
    spawnEndingCreditsTumblingSnowboard: after the copy-direction rewrite, the only residual was
    `sp1C` at 0x18 instead of 0x1c; swapping its declaration with temp_v0's certified exact.
    """
    if not stack_slot_faults(diff):
        return []
    begin = region[1]
    decls = []
    for m in LOCAL_DECL.finditer(masked, begin, region[2]):
        if "=" in masked[m.start():m.end()] or "(" in masked[m.start():m.end()]:
            break
        if decls and m.start() != decls[-1][1]:
            gap = masked[decls[-1][1]:m.start()]
            if gap.strip():
                break
        decls.append((m.start(), m.end()))
    out = []
    for (a0, a1), (b0, b1) in zip(decls, decls[1:]):
        first, second = source[a0:a1], source[b0:b1]
        gap = source[a1:b0]
        number = source.count("\n", 0, a0) + 1
        out.append(Edit("declorder", f"swap `{first.strip()}` / `{second.strip()}`", number, a0, b1,
                        second + gap + first))
    return out


def _subscript_spellings(source, masked, number, start, stop, strides):
    """Same address, other spelling: `&A[e]` -> `(A + (e))` or `((u8 *)(A) + (e) * S)`; `A[e]` -> `*(A + (e))`.

    The byte form takes S from the target's shift (`sll 4` -> 16), so a stride the declaration gets
    wrong can be stated without a new type. Measured: waitForCourseGateTrigger, `[(i) * 4]` produced the
    right instructions plus an extra temporary; the hand-written byte form was exact.
    """
    out = []
    for m in re.finditer(r"(&\s*)?\b([A-Za-z_]\w*)\s*\[", masked[start:stop]):
        name_start = start + m.start(2)
        open_at = start + m.end() - 1
        depth, close = 0, None
        for i in range(open_at, len(masked)):
            if masked[i] == "[":
                depth += 1
            elif masked[i] == "]":
                depth -= 1
                if depth == 0:
                    close = i
                    break
        if close is None or masked[close + 1:close + 2] == "[":
            continue
        base, index = m.group(2), source[open_at + 1:close]
        if m.group(1):
            span = (start + m.start(1), close + 1)
            out.append(Edit("spelling", f"&{base}[..] -> pointer sum", number, *span, f"({base} + ({index}))"))
            for stride in sorted(strides):
                out.append(Edit("spelling", f"&{base}[..] -> byte offset x{stride}", number, *span,
                                f"((u8 *)({base}) + ({index}) * {stride})"))
        else:
            out.append(Edit("spelling", f"{base}[..] -> *(pointer sum)", number, name_start, close + 1,
                            f"(*({base} + ({index})))"))
    return out


def _pool_edits(source, function, diff, lines: set[int]):
    """The existing shape-specific rewrite pool, kept only where it touches an attributed line."""
    from solver import rewrites, residual_sites
    out = []
    try:
        proposals = rewrites.propose(source, diff)
    except Exception:
        return out
    for rewrite in proposals:
        try:
            child = rewrite(source)
        except Exception:
            continue
        if child == source:
            continue
        region = residual_sites.edit_region(source, child)
        if not any(region["start_line"] <= n <= region["end_line"] for n in lines):
            continue
        tail = len(source) - region["stop"]
        out.append(Edit(f"pool:{rewrite.kind}", rewrite.label, region["start_line"], region["start"],
                        region["stop"], child[region["start"]:len(child) - tail]))
    return out


def _shape_edits(source, function, diff):
    """solver.branch_shape repairs as whole-region edits, so control flow composes with typed edits.

    branch_shape was reachable only through register search (regalloc_mutations.variants), and
    register search never ran on 58 of the 75 unsolved functions where it fires: 202 of its 203
    variants there had never been compiled. Compiled once each they improved 52 of 75 functions and
    broke none (eval/results/branch-routing-20260929). Each generator is already gated on the
    residual signature it was built for, so no attributed line is required.
    """
    from solver import branch_shape, residual_sites
    out = []
    try:
        variants = list(branch_shape.variants(source, function, diff))
    except Exception:
        return out
    from solver import unaligned_copy     # catalog ido53-unaligned-struct-copy; gated on target lwl/lwr
    try:
        variants += [(label, "unaligned_copy", child) for label, child in unaligned_copy.variants(source, function, diff)]
    except Exception:
        pass
    from solver import temp_copyback      # catalog ido53-o1-copyback-temporary; gated on extra stack traffic
    try:
        variants += [(label, "temp_copyback", child) for label, child in temp_copyback.variants(source, function, diff)]
    except Exception:
        pass
    from solver import counted_loop       # catalog ido53-o1-counted-loop-shape
    try:
        variants += [(label, "counted_loop", child) for label, child in counted_loop.variants(source, function, diff)]
    except Exception:
        pass
    for label, kind, child in variants:
        if child == source:
            continue
        region = residual_sites.edit_region(source, child)
        tail = len(source) - region["stop"]
        out.append(Edit(f"shape:{kind}", label, region["start_line"], region["start"], region["stop"],
                        child[region["start"]:len(child) - tail]))
    return out


def _gap_edits(source, function, diff, attribution):
    """Opt-in (`gaps=True`) generators for planted-edit gaps (eval/results/edit-capability-20261002):
    solver.missing_store writes a store the target performs and the candidate lacks, from the target's own
    instructions; solver.next_use_temp inlines `T = E; S(T);` (E may be a call), gated on extra stack traffic."""
    from solver import missing_store, next_use_temp, residual_sites, rewrite_library
    out, variants = [], []
    for kind, make in (("missing_store", lambda: missing_store.variants(source, function, diff, attribution)),
                       ("next_use_temp", lambda: next_use_temp.variants(source, function, diff)),
                       ("empty_arm", lambda: [(label, child) for _k, label, child
                                              in rewrite_library.empty_arm_drops(source, function)])):
        try:
            variants += [(kind, label, child) for label, child in make()]
        except Exception:
            continue
    for kind, label, child in variants:
        region = residual_sites.edit_region(source, child)
        tail = len(source) - region["stop"]
        out.append(Edit(f"shape:{kind}", label, region["start_line"], region["start"], region["stop"],
                        child[region["start"]:len(child) - tail]))
    return out


def _commutative_edits(source, function, lines: set[int]):
    """regalloc_mutations.commutative_swaps (register search only until now), kept where it touches an attributed
    line. IDO emits a commutative operation's operand LOADS in source order, so `a->x + a->y` and `a->y + a->x`
    differ in load order (planted commute on drawEndingCreditsCharacterLoopingSparkle, edit-capability-20261002)."""
    from solver import regalloc_mutations, residual_sites
    out = []
    try:
        variants = list(regalloc_mutations.commutative_swaps(source, function))
    except Exception:
        return out
    for label, _kind, child in variants:
        region = residual_sites.edit_region(source, child)
        if not any(region["start_line"] <= n <= region["end_line"] for n in lines):
            continue
        tail = len(source) - region["stop"]
        out.append(Edit("commutative", label, region["start_line"], region["start"], region["stop"],
                        child[region["start"]:len(child) - tail]))
    return out


WIDTH_OF = {"s8": 8, "u8": 8, "s16": 16, "u16": 16, "s32": 32, "u32": 32}


def widening_hint(diff: str) -> set[str]:
    """Narrow types the CANDIDATE re-extends and the target does not: candidate-only `sll 0x10` + `sra 0x10` means an
    s16 the target holds wider (`andi 0xffff` u16; 0x18 / `andi 0xff` the 8-bit pair). Ranking only."""
    from solver import diffrepair
    target, candidate = diffrepair._streams(diff or "")

    def kinds(stream):
        # COUNTED, not a set: a target that extends some other s16 elsewhere must not cancel a candidate-only one.
        # The shift pair need not be adjacent: IDO schedules other work between them (planted cast_width,
        # initControllerPakRaceRecordSaveExitMessage: `sll t8,a1,0x10` ... four instructions ... `sra a1,t8,0x10`).
        found = Counter()
        for i, a in enumerate(stream):
            sll = re.match(r"^sll\s+(\w+),\w+,0x(10|18)$", a)
            if not sll:
                continue
            for b in stream[i + 1:i + 8]:
                m = re.match(rf"^sr([al])\s+\w+,{sll.group(1)},0x{sll.group(2)}$", b)
                if m:
                    found[("s" if m.group(1) == "a" else "u") + ("16" if sll.group(2) == "10" else "8")] += 1
                    break
        for x in stream:
            m = re.match(r"^andi\s+\w+,\w+,0x(ffff|ff)$", x)
            if m:
                found["u16" if m.group(1) == "ffff" else "u8"] += 1
        return found
    return set(kinds(candidate) - kinds(target))


def _widening_first(edits: list[Edit], hint: set[str]) -> list[Edit]:
    """Stable: type/decl edits that widen a hinted narrow type move ahead of the rest of their family."""
    if not hint:
        return edits
    def key(edit):
        m = re.search(r"(\w+) -> (\w+)(?: \(x\d+\))?$", edit.label)
        if edit.kind in ("decl", "type") and m:
            old, new = SPELLED.get(m.group(1), m.group(1)), m.group(2)
            if old in hint and WIDTH_OF.get(new, 0) > WIDTH_OF.get(old, 99):
                return 0
        return 1
    return sorted(edits, key=key)


def _mined_edits(source, function, diff, attribution=None, limit: int = 8):
    """Rewrites ordered by the mined rule table (solver.rule_miner): Engine A generic rewrites and Engine B templates,
    localised to the residual's source lines when attribution is verified."""
    from solver import residual_sites, rule_miner
    out = []
    sites, _status = site_lines(diff, attribution)
    try:
        props = rule_miner.proposals(source, function, diff, limit=limit, sites=sites or None)
    except Exception:
        return out
    for score, key, label, child in props:
        region = residual_sites.edit_region(source, child)
        tail = len(source) - region["stop"]
        out.append(Edit(f"mined:{key}", f"{label} ({score:.2f})", region["start_line"], region["start"], region["stop"],
                        child[region["start"]:len(child) - tail]))
    return out


def _interleaved(edits: list[Edit]) -> list[Edit]:
    """Round-robin across edit families, keeping each family's own order.

    A strict family order starved the late families: on the width frame (eval/results/
    width-edits-20260929) 85 functions had `decl`/`type` edits proposed and none compiled, because
    literal, copy-direction and read-back edits filled the 24-compile level first.
    """
    families: dict[str, list[Edit]] = {}
    for edit in edits:
        families.setdefault(edit.kind.split(":")[0], []).append(edit)
    queues = list(families.values())
    out = []
    for i in range(max(map(len, queues), default=0)):
        out.extend(q[i] for q in queues if i < len(q))
    return out


def _grouped(source: str, edits: list[Edit]) -> list[Edit]:
    """The same token change at every site that offers it, as one edit.

    Correlated sites are the norm, not the exception: one element type feeds two subscripts, one
    field type feeds two stores. Applied at one site alone such an edit can LOWER the score (the
    other site still disagrees, and the search keeps only improvements), so the greedy step never
    reaches the fix. Grouping costs one compile per distinct change.
    """
    groups: dict[tuple, list[Edit]] = {}
    for edit in edits:
        groups.setdefault((edit.kind, source[edit.start:edit.stop], edit.text), []).append(edit)
    out = []
    for (kind, before, after), members in groups.items():
        spans = sorted({(e.start, e.stop, e.text) for e in members})
        if len(spans) < 2 or any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
            continue
        first = spans[0]
        out.append(Edit(kind, f"{members[0].label} (x{len(spans)})", members[0].line,
                        first[0], first[1], first[2], tuple(spans[1:])))
    return out


ORDER = {"shape": -1, "literal": 0, "copydir": 1, "declorder": 1, "readback": 1, "decl": 2, "type": 3,
         "operator": 3, "spelling": 4, "scale": 5, "uncast": 6}


def propose(source: str, function: str, diff: str, attribution: dict | None,
            *, max_sites: int = 4, only=None, mined: bool = False, operators: bool = False,
            gaps: bool = False) -> tuple[list[Edit], dict]:
    """Ranked typed edits at the attributed sites, with a receipt that says why when there are none.

    `only` (an instruction predicate, from solver.residual_classes.focus) ranks the lines that carry the
    fault class being repaired ahead of the rest. Unfocused, the four heaviest lines are the ones with the
    most mismatches, which are knock-on: in the class-key trial 145 of 207 searches stalled on the width
    class with its lines never selected (eval/results/composed-edits-20260929).
    """
    weights, status = site_lines(diff, attribution)
    if only is not None and weights:
        focused, _ = site_lines(diff, attribution, only)
        weights = Counter({line: w + (10**6 if line in focused else 0) for line, w in (weights + focused).items()})
    receipt = {"attribution": status, "sites": dict(weights.most_common(max_sites))}
    region = code_shapes._body(source, function)
    shape = _shape_edits(source, function, diff)
    if gaps:
        shape += _gap_edits(source, function, diff, attribution)
    receipt["shape_edits"] = len(shape)
    mined_lane = _mined_edits(source, function, diff, attribution) if mined else []
    receipt["mined_edits"] = len(mined_lane)
    if not weights or region is None:
        receipt["declined"] = status if not weights else "function body not found"
        if shape or mined_lane:
            receipt["proposals"] = len(shape) + len(mined_lane)
        return shape + mined_lane, receipt
    masked = c89._mask(source)
    spans = _line_spans(source)
    moves, wanted, factors, strides = deltas(diff)
    receipt.update(literal_deltas=sorted(moves), scale_factors=sorted(factors), strides=sorted(strides))
    edits: list[Edit] = []
    for rank, (number, _weight) in enumerate(weights.most_common(max_sites)):
        if not 1 <= number <= len(spans):
            continue
        start, stop = spans[number - 1]
        batch = (_literal_edits(source, masked, number, start, stop, moves, wanted)
                 + _declaration_edits(source, masked, function, number, start, stop, region)
                 + _type_edits(source, masked, number, start, stop)
                 + _copy_direction_edits(source, masked, number, start, stop, region)
                 + _readback_edits(source, masked, number, start, stop, region)
                 + _subscript_spellings(source, masked, number, start, stop, strides)
                 + _scale_edits(source, masked, number, start, stop, factors)
                 + _uncast_edits(source, masked, number, start, stop))
        if operators:
            batch += _operator_edits(source, masked, number, start, stop)
        edits.extend((rank, e) for e in batch)
    # Frame-slot faults name no source line (the declaration is not where the access is), so the
    # declaration-order swaps are offered whenever the residual has them.
    edits.extend((0, e) for e in _declaration_order_edits(source, masked, region, diff))
    unique, seen = [], set()
    lines = set(dict(weights.most_common(max_sites)))
    pool = [(max_sites, e) for e in _pool_edits(source, function, diff, lines)]
    ranked = [(-1, e) for e in shape] + edits + pool
    for _rank, edit in sorted(ranked, key=lambda re_: (re_[0], ORDER.get(re_[1].kind.split(":")[0], 9))):
        key = (edit.start, edit.stop, edit.text)
        if key not in seen:
            seen.add(key)
            unique.append(edit)
    if gaps:
        have = {(e.start, e.stop, e.text) for e in unique}
        unique += [e for e in _commutative_edits(source, function, lines) if (e.start, e.stop, e.text) not in have]
    combined = _grouped(source, unique) + unique
    if gaps:
        # After grouping: grouped edits are prepended, so a hint applied before them is overridden.
        combined = _widening_first(combined, widening_hint(diff))
    unique = _interleaved(combined)
    # Shape repairs (branch_shape, unaligned_copy, temp_copyback, counted_loop) are a priority lane, not one family
    # among six. They are few (about 3 per state) and gated on their own residual signature, and on osMotorStart
    # they were the only edits that moved the score. Round-robin gave them one slot in six, and 20 of level 1's 24
    # compiles went to typed edits that left it unchanged (eval/results/chain-vs-search-20260929).
    unique = [e for e in unique if e.kind.startswith("shape:")] + [e for e in unique if not e.kind.startswith("shape:")]
    if mined_lane:
        # The mined lane (solver.rule_miner) follows the shape lane, best-scored first, ahead of typed edits.
        n_shape = sum(1 for e in unique if e.kind.startswith("shape:"))
        have = {(e.start, e.stop, e.text) for e in unique}
        unique = unique[:n_shape] + [e for e in mined_lane if (e.start, e.stop, e.text) not in have] + unique[n_shape:]
    receipt["proposals"] = len(unique)
    if not unique:
        receipt["declined"] = ("sites carry no literal, integer type, declared identifier, subscript, cast, "
                               "stored temporary, frame-slot fault, or pool rewrite")
    return unique, receipt


# ---------------------------------------------------------------------------- search

def distances(diff: str) -> tuple[int, int]:
    """(instruction distance, register distance); defined in `signals` so every route can report it."""
    return signals.distances(diff)


def gradient(attempt) -> tuple:
    """Lexicographic progress: compiled, then instruction distance, then register distance.

    The similarity score charges a register rename like any other wrong instruction, so an edit that
    fixes a stride but shifts the temporaries (measured: waitForCourseGateTrigger, sll 2 -> sll 4 correct,
    score 99.811 -> 98.302) looks like a regression and is discarded. Register-only residue is what
    `regalloc_search` closes, so instructions are settled first.
    """
    if not attempt.compiled:
        return (1, 10**9, 10**9)
    if attempt.exact:
        return (0, 0, 0)
    return (0, *distances(attempt.diff or ""))


def search(score, source: str, function: str, *, budget: int = 72, depth: int = 3,
           per_step: int = 24, beam: int = 3, key=None, focus=None, mined: bool = True,
           operators: bool = False, gaps: bool = False, reorder=None, escalate: bool = False) -> dict:
    """Beam search over typed edits. `score(code, label, parent_code)` compiles and returns a
    workspace.Attempt; `parent_code` is the source the edit was applied to (None for the baseline), so
    callers can record true parent edges.

    Each level expands every frontier member (children interleaved, `per_step` compiles per level)
    and keeps the `beam` best children that beat their own parent on `gradient`. A single best-first
    path was measured to discard the right step: on spawnEndingCreditsTumblingSnowboard the
    copy-direction child (0, 4, 0; its residual one frame slot, fixed next by declaration order) lost
    to a re-read child (0, 0, 8) whose register residue the register protocol finds unreachable.
    """
    from solver import workspace
    # `key` orders children (default: gradient). solver.residual_classes.key fixes one fault class at a
    # time; the returned *_gradient fields and register_only stay on `gradient` so callers read them as before.
    # `reorder(parent_diff, edits) -> edits` reorders (or filters) one parent's proposals before they are compiled:
    # solver.search_priors.orderer. None keeps propose()'s order.
    # `escalate`: `per_step` becomes a TIER, not a cap. A tier that improves nothing is a miss, and the level pulls
    # the next tier of the same proposals (until `budget`); a tier with an improving child moves to the next level.
    # Easy residuals pay one narrow tier per level, hard ones the full width.
    rank = key or gradient
    trail = []
    current = score(source, "baseline", None)
    if workspace.repair_complete(current):
        # the starting source is already complete under today's toolchain: report it. The loop below only
        # breaks on it, and the result said "exact": False (2026-09-30: setCurrentGameTaskCallback)
        return {"exact": True, "source": source, "compiles": 0, "trail": trail, "baseline": current.score,
                "best": current.score, "baseline_gradient": [0, 0, 0], "best_gradient": [0, 0, 0]}
    best_code, best = source, current
    frontier = [(source, current)]
    spent = 0
    seen = {source}
    for level in range(depth):
        if workspace.repair_complete(best) or spent >= budget or not frontier:
            break
        queues = []
        for parent_code, parent in frontier:
            only = focus(parent.diff or "") if focus else None
            edits, receipt = propose(parent_code, function, parent.diff or "", parent.source_attribution, only=only,
                                     mined=mined, operators=operators, gaps=gaps)
            if reorder is not None:
                edits = reorder(parent.diff or "", edits)
                receipt["ordered"] = len(edits)
            trail.append({"level": level, **receipt})
            queues.append([(parent_code, parent, e) for e in edits])
        order = [item for group in zip(*[q + [None] * (max(map(len, queues)) - len(q)) for q in queues])
                 for item in group if item is not None] if queues else []
        children = []
        used = 0
        for parent_code, parent, edit in order:
            if spent >= budget:
                break
            if used >= per_step:
                if not (escalate and not children):
                    break
                used = 0
                trail.append({"level": level, "escalated": True, "spent": spent})
            child = edit.apply(parent_code)
            if child in seen:
                continue
            seen.add(child)
            spent += 1
            used += 1
            attempt = score(child, f"{edit.kind}:{edit.label}"[:110], parent_code)
            trail.append({"level": level, "edit": edit.label, "kind": edit.kind,
                          "compiled": attempt.compiled, "score": attempt.score,
                          "gradient": list(gradient(attempt)), "key": list(rank(attempt)) if attempt.compiled else None,
                          "complete": workspace.repair_complete(attempt)})
            if workspace.repair_complete(attempt):
                return {"exact": True, "source": child, "compiles": spent, "trail": trail,
                        "baseline": current.score, "best": attempt.score,
                        "baseline_gradient": list(gradient(current)), "best_gradient": [0, 0, 0]}
            if attempt.compiled and rank(attempt) < rank(parent):
                children.append((rank(attempt), child, attempt))
        children.sort(key=lambda c: c[0])
        frontier = [(code, attempt) for _g, code, attempt in children[:beam]]
        if frontier and rank(frontier[0][1]) < rank(best):
            best_code, best = frontier[0]
    return {"exact": False, "source": best_code, "compiles": spent, "trail": trail,
            "baseline": current.score, "best": best.score,
            "baseline_gradient": list(gradient(current)), "best_gradient": list(gradient(best)),
            "register_only": gradient(best)[:2] == (0, 0) and gradient(best)[2] > 0}
