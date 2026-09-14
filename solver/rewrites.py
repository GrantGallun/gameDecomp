"""Candidate source rewrites proposed from the oracle's residual.

Two matches were closed by hand after an external review, and both needed a
PAIR of rewrites -- neither reached exact alone:

    updateRaceSplitscreenSelectPlayerCountIcons  loop bound 4->5 + 8 bytes pad
    updateEndingLindaExitUntilPhase3C            swap two args   + 2 bytes pad

Two of those four had no generator in the codebase at all. diffrepair can move
a field, retype one, or reorder a struct; nothing could change a constant or
permute call arguments, so those matches were unreachable however long the
loop ran.

This module proposes rewrites; it decides nothing. Every proposal is handed to
the oracle, and a wrong one simply fails to verify. That is deliberate -- the
residual states what the target does, but mapping an assembly constant back to
the source literal that produced it is inference, and inference here gets
checked rather than trusted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from solver import c89, diffrepair, signals

MEM = signals.MEM
OPCODE = signals.OPCODE
REGNAME = re.compile(r"\$?\b([av][0-9]|t[0-9]|s[0-8])\b")
ALLOC_REGISTER = re.compile(
    r"\$?\b(?:zero|at|v[01]|a[0-3]|t[0-9]|s[0-8]|k[01]|gp|sp|fp|ra)\b")
IMMEDIATE = re.compile(r"(-?0x[0-9a-fA-F]+|-?\b\d+\b)")


@dataclass
class Rewrite:
    label: str
    kind: str                    # immediate | argswap | layout | signedness
    apply: Callable[[str], str]

    def __call__(self, code: str) -> str:
        return self.apply(code)


def _num(text: str):
    try:
        return int(text, 16) if text.lower().startswith(("0x", "-0x")) \
            else int(text)
    except ValueError:
        return None


def immediate_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Constants the target uses where the candidate used another.

        -slti at,v1,5      the target compares against 5
        +slti at,v1,4      we wrote 4

    The assembly constant is a fact; WHICH source literal produced it is not,
    so every plausible occurrence is proposed separately and the oracle picks.
    A literal appearing many times is skipped -- rewriting all of them is a
    different edit, and rewriting an arbitrary one is a coin flip.
    """
    out: list[Rewrite] = []
    seen: set[tuple[int, int]] = set()
    pairs, _n, _m = signals._pairs(diff)
    for a, b in pairs:
        oa = OPCODE.match(a).group(1) if OPCODE.match(a) else ""
        ob = OPCODE.match(b).group(1) if OPCODE.match(b) else ""
        if oa != ob or MEM.match(a) or MEM.match(b):
            continue
        if REGNAME.findall(a) != REGNAME.findall(b):
            continue                       # a register difference, not a value
        want = [x for x in IMMEDIATE.findall(a) if _num(x) is not None]
        got = [x for x in IMMEDIATE.findall(b) if _num(x) is not None]
        if len(want) != len(got):
            continue
        for w, g in zip(want, got):
            nw, ng = _num(w), _num(g)
            if nw is None or ng is None or nw == ng or (nw, ng) in seen:
                continue
            seen.add((nw, ng))
            for pattern in (str(ng), hex(ng)):
                # a bare literal, not part of a longer number or identifier
                rx = re.compile(r"(?<![\w.])" + re.escape(pattern) + r"(?![\w.])")
                hits = rx.findall(code)
                if len(hits) != 1:
                    continue               # ambiguous or absent: do not guess
                repl = str(nw) if pattern == str(ng) else hex(nw)
                out.append(Rewrite(
                    f"immediate {pattern} -> {repl}", "immediate",
                    lambda s, _rx=rx, _r=repl: _rx.sub(_r, s, count=1)))
    return out


SIGNED_COMPARE = {("slt", "sltu"), ("slti", "sltiu")}
SIGNED_ARITHMETIC = {("div", "divu")}
UNSIGNED_DECL = re.compile(r"\b(u32|unsigned\s+(?:int|long))\s+([A-Za-z_]\w*)\b")
U32_TOKEN = re.compile(r"\bu32\b")
HIGH_U32_LITERAL = re.compile(r"(?<![\w.])0[xX]([0-9a-fA-F]{8})(?:[uUlL]+)?(?![\w.])")


def signed_compare_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Repair signed target comparisons/division emitted unsigned by a candidate.

        -slt  at,a1,v0       target compares signed values
        +sltu at,a1,v0       candidate applied the usual unsigned conversion

    One source edit is often insufficient.  A ``u32`` field makes the left
    operand unsigned, while a high-bit literal such as ``0xFFCE0000`` is
    itself unsigned in C89 even when the field is ``s32``.  The repair search
    composes proposals, so expose both levers independently: retype one
    unsigned declaration, or spell one 32-bit high-bit literal as its signed
    two's-complement value.  The oracle decides which declarations/literals
    actually feed the comparison.

    These edits can change source-level semantics and therefore are
    hypotheses, not facts.  They are proposed only when the residual contains
    the exact signed/unsigned opcode pair with identical register operands.
    """
    mismatch = False
    for target, candidate in signals._pairs(diff)[0]:
        mt, mc = OPCODE.match(target), OPCODE.match(candidate)
        if not (mt and mc):
            continue
        if (mt.group(1), mc.group(1)) not in SIGNED_COMPARE | SIGNED_ARITHMETIC:
            continue
        if signals._regs(target) != signals._regs(candidate):
            continue
        mismatch = True
        break
    if not mismatch:
        return []

    out: list[Rewrite] = []
    masked = c89._mask(code)
    for match in UNSIGNED_DECL.finditer(masked):
        line_start = masked.rfind("\n", 0, match.start()) + 1
        if re.search(r"\btypedef\b", masked[line_start:match.start()]):
            continue
        a, b = match.span(1)
        out.append(Rewrite(
            f"signed declaration {match.group(2)} at {a}", "signedness",
            lambda source, _a=a, _b=b: source[:_a] + "s32" + source[_b:]))

    u32_spans: list[tuple[int, int]] = []
    for match in U32_TOKEN.finditer(masked):
        line_start = masked.rfind("\n", 0, match.start()) + 1
        if re.search(r"\btypedef\b", masked[line_start:match.start()]):
            continue
        u32_spans.append(match.span())
        # Named declarations already have a more informative proposal above,
        # but an unnamed prototype parameter (`void f(u32, u32)`) has no name
        # and was otherwise unreachable.  Duplicate source states are removed
        # by faultsearch's `seen` set.
        a, b = match.span()
        out.append(Rewrite(
            f"signed u32 token at {a}", "signedness",
            lambda source, _a=a, _b=b: source[:_a] + "s32" + source[_b:]))

    literal_edits: list[tuple[int, int, str]] = []
    for match in HIGH_U32_LITERAL.finditer(masked):
        value = int(match.group(1), 16)
        if value < 0x80000000:
            continue
        signed = value - 0x100000000
        # -0x80000000 is parsed as unary minus applied to an unsigned literal
        # by C89 compilers.  The explicit cast is the unambiguous spelling.
        replacement = ("(s32)0x80000000" if signed == -0x80000000
                       else f"-0x{-signed:x}")
        a, b = match.span()
        literal_edits.append((a, b, replacement))
        out.append(Rewrite(
            f"signed literal {match.group(0)} -> {replacement} at {a}",
            "signedness",
            lambda source, _a=a, _b=b, _r=replacement:
            source[:_a] + _r + source[_b:]))

    # A signed comparison is frequently a type FAMILY error, not one token:
    # the field, its call-site prototype, and both appearances of a clamp
    # constant must agree before IDO forms the target web.  Intermediate edits
    # can have an unchanged or worse residual and are pruned by a narrow beam.
    # Offer the bounded atomic hypothesis as well as the surgical edits.
    if 0 < len(u32_spans) <= 12 and 0 < len(literal_edits) <= 4:
        edits = [(a, b, "s32") for a, b in u32_spans] + literal_edits

        def apply_bundle(source: str, _edits=tuple(edits)) -> str:
            for start, end, replacement in sorted(_edits, reverse=True):
                source = source[:start] + replacement + source[end:]
            return source

        out.append(Rewrite(
            f"signed comparison family ({len(u32_spans)} u32, "
            f"{len(literal_edits)} literal)",
            "signedness", apply_bundle))
    return out


CALL = re.compile(r"(\w+)\s*\(([^;()]*(?:\([^()]*\)[^;()]*)*)\)\s*;", re.S)


def argswap_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Adjacent call arguments the target loads into the opposite registers.

        -lh a2,0x26(s0)  -lh a1,0x24(s0)      target: a2<-0x26, a1<-0x24
        +lh a1,0x26(s0)  +lh a2,0x24(s0)      ours:   the reverse

    Same opcode, same offsets, destination registers exchanged: the arguments
    are in the wrong order. Which call is not stated, so every call with enough
    arguments gets a proposal per adjacent pair and the oracle decides.
    """
    swapped = False
    pairs, _n, _m = signals._pairs(diff)
    for a, b in pairs:
        ma, mb = MEM.match(a), MEM.match(b)
        if not (ma and mb):
            continue
        if ma.group(1) == mb.group(1) and ma.group(3) == mb.group(3) \
                and ma.group(2) != mb.group(2):
            swapped = True
            break
    if not swapped:
        return []

    out: list[Rewrite] = []
    for m in CALL.finditer(code):
        line_start = code.rfind("\n", 0, m.start()) + 1
        prefix = code[line_start:m.start()]
        if re.search(r"\bextern\b", prefix):
            continue                       # a prototype, not a call site
        args = [a.strip() for a in _split_args(m.group(2))]
        if len(args) < 2:
            continue
        for i in range(len(args) - 1):
            new_args = list(args)
            new_args[i], new_args[i + 1] = new_args[i + 1], new_args[i]
            old, new = m.group(0), (m.group(1) + "(" + ", ".join(new_args)
                                    + ");")
            if old == new:
                continue
            out.append(Rewrite(
                f"swap args {i}/{i+1} of {m.group(1)} at {m.start()}",
                "argswap",
                lambda s, _o=old, _n2=new: s.replace(_o, _n2, 1)))
    return out


def _split_args(text: str) -> list[str]:
    """Split a call's argument list on commas at depth zero."""
    out, depth, cur = [], 0, []
    for ch in text:
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


RELOC_LOAD = re.compile(
    r"^(?:lw|lh|lhu|lb|lbu|ld)\s+[^,]+,.*%lo\(\s*([A-Za-z_]\w*)")


def pointer_table_deref_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Remove ``&`` when the target loads a pointer-table entry.

        target:    lw v0,%lo(pointerTable)(v0)
        candidate: addiu t8,t8,%lo(pointerTable)

    Given ``p = &pointerTable[i]``, the candidate computes the address of the
    slot.  The target's relocation-bearing load says it wants the pointer
    stored in that slot, i.e. ``p = pointerTable[i]``.  This only fires when
    the target has such a load, the candidate side mentions the same symbol
    but lacks the load, and the source contains the exact address-of-indexed
    spelling.  The oracle verifies the inferred dereference.
    """
    minus = [line[1:].strip() for line in diff.splitlines()
             if line.startswith("-") and not line.startswith("---")]
    plus = [line[1:].strip() for line in diff.splitlines()
            if line.startswith("+") and not line.startswith("+++")]
    symbols = {match.group(1) for line in minus
               if (match := RELOC_LOAD.match(line))}
    out: list[Rewrite] = []
    masked = c89._mask(code)
    for symbol in sorted(symbols):
        if not any(f"%lo({symbol}" in line for line in plus):
            continue
        if any((match := RELOC_LOAD.match(line))
               and match.group(1) == symbol for line in plus):
            continue
        pattern = re.compile(r"&\s*(?=" + re.escape(symbol) + r"\s*\[)")
        for match in pattern.finditer(masked):
            a, b = match.span()
            out.append(Rewrite(
                f"dereference pointer table {symbol} at {a}", "pointer-table",
                lambda source, _a=a, _b=b: source[:_a] + source[_b:]))
    return out


RELOC_ADDEND = re.compile(r"%(?:hi|lo)\(\s*(\w+)\s*(?:\+\s*(-?(?:0x)?[0-9a-fA-F]+))?\s*\)")
RELOC_REF = re.compile(
    r"%(hi|lo)\(\s*([A-Za-z_]\w*)\s*"
    r"(?:\+\s*(-?(?:0x)?[0-9a-fA-F]+))?\s*\)")


def reloc_padding_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Padding derived from a relocation ADDEND, which diffrepair cannot see.

        -lbu t7,%lo(gRacePlayers+8)(t7)     target reads 8 bytes into the entry
        +lbu t7,%lo(gRacePlayers)(t7)       we read it at 0

    The difference is carried in the relocation's addend rather than in an
    instruction offset operand, so the offset machinery finds nothing at all --
    diffrepair derives zero constraints from this residual. It is nevertheless
    a plain statement that the field accessed at addend M belongs at addend N,
    so N - M bytes are missing ahead of it in the element struct.

    This was the last gap blocking updateRaceSplitscreenSelectPlayerCountIcons,
    whose entire residual is one such line.
    """
    out: list[Rewrite] = []
    seen: set[tuple[str, int]] = set()
    pairs, _n, _m = signals._pairs(diff)
    for a, b in pairs:
        ma, mb = RELOC_ADDEND.search(a), RELOC_ADDEND.search(b)
        if not (ma and mb) or ma.group(1) != mb.group(1):
            continue
        want = _num(ma.group(2)) if ma.group(2) else 0
        got = _num(mb.group(2)) if mb.group(2) else 0
        if want is None or got is None or want == got or want < got:
            continue
        sym, delta = ma.group(1), want - got
        if (sym, delta) in seen:
            continue
        seen.add((sym, delta))

        body = _element_struct_body(code, sym)
        if body is None:
            continue
        fields = diffrepair._fields("struct S {" + body + "};")
        target_field = next((m for m, off, _s in fields if off == got), None)
        if target_field is None:
            continue
        old_line = target_field.group(0)
        pad = (f"{target_field.group('indent')}"
               f"char rpad{got:02x}[{delta:#x}];\n")
        out.append(Rewrite(
            f"{delta} bytes before {sym}.{target_field.group('name')}",
            "layout",
            lambda s, _o=old_line, _p=pad: s.replace(_o, _p + _o, 1)))
    return out


def reloc_symbol_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Replace one source symbol when the residual names a different one.

        -lui t3,%hi(D_2003538)       target address
        +lui t1,%hi(gAssetHandles)   candidate address

    The target symbol is binary evidence, not reference C. What remains
    uncertain is which occurrence of the candidate symbol produced that
    relocation, so each non-declaration occurrence is proposed separately and
    checked by the oracle.

    A replacement often needs a declaration to compile. We clone the old
    symbol's ``extern`` declaration instead of inventing a type; if no such
    declaration exists, the rewrite declines. This keeps the transformation
    bounded and preserves the expression's original C type while the oracle
    judges whether the address change is correct.
    """
    evidence: set[tuple[str, str]] = set()
    pairs, _n, _m = signals._pairs(diff)
    for target, candidate in pairs:
        target_refs = RELOC_REF.findall(target)
        candidate_refs = RELOC_REF.findall(candidate)
        if len(target_refs) != len(candidate_refs):
            continue
        for want, got in zip(target_refs, candidate_refs):
            want_kind, want_sym, _want_addend = want
            got_kind, got_sym, _got_addend = got
            if want_kind == got_kind and want_sym != got_sym:
                evidence.add((got_sym, want_sym))

    out: list[Rewrite] = []
    masked = c89._mask(code)
    for got, want in sorted(evidence):
        got_decl = re.search(
            r"(?m)^[ \t]*extern\b[^\n;]*\b" + re.escape(got)
            + r"\b[^\n;]*;", masked)
        if not got_decl:
            continue
        old_decl = code[got_decl.start():got_decl.end()]
        new_decl = re.sub(r"\b" + re.escape(got) + r"\b", want,
                          old_decl, count=1)
        want_declared = bool(re.search(
            r"(?m)^[ \t]*extern\b[^\n;]*\b" + re.escape(want)
            + r"\b[^\n;]*;", masked))

        token = re.compile(r"\b" + re.escape(got) + r"\b")
        for match in token.finditer(masked):
            # Never replace the declaration itself. Source candidates declare
            # globals before their function bodies, so also refuse an unusual
            # use-before-declaration rather than adjusting ambiguous offsets.
            if match.start() <= got_decl.end():
                continue
            changed = (code[:match.start()] + want + code[match.end():])
            if not want_declared:
                changed = (changed[:got_decl.end()] + "\n" + new_decl
                           + changed[got_decl.end():])
            label = f"reloc symbol {got} -> {want} at {match.start()}"
            out.append(Rewrite(
                label, "reloc",
                lambda source, _old=code, _new=changed:
                _new if source == _old else source))
    return out


MASK_OP = re.compile(r"^(andi)\s+\$?(\w+),\s*\$?(\w+),\s*(0x[0-9a-fA-F]+|\d+)")
# EXACTLY a byte or halfword mask. `0[xX][fF]{2,4}` also admitted 0xFFF, which
# is a 12-bit mask and NOT a width no-op: dropping it changes meaning, and the
# sweep duly proposed `drop mask 0xFFF` edits that scored better while being
# semantically different. The safety argument for this rewrite is that lhu and
# lbu already zero-extend, and that argument only covers 0xFF and 0xFFFF.
SOURCE_MASK = re.compile(r"\s*&\s*(0[xX](?:[fF]{2}|[fF]{4})|255|65535)\b")


def drop_mask_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Remove a redundant mask the candidate emits and the target does not.

        +andi v0,t6,0xffff       we mask; the target never does
        -sllv t0,t9,t6           and then the target uses the UNMASKED value
        +sllv t0,t9,v0

    An extra instruction displaces every later branch, which is why
    requestRumbleMotorStart shows three "structural" faults for what is really
    one surplus `& 0xFFFF`: its branch targets all shift by four.

    The catalogue already holds the opposite lever -- adding a redundant mask
    to advance IDO's temp FIFO -- and there was no generator for removing one,
    so this residual proposed nothing at all.

    Masking after a narrow load is semantically a no-op (lhu and lbu already
    zero-extend), so dropping one cannot change meaning; if the mask was load
    bearing the candidate simply fails to verify.
    """
    extra_mask = False
    for line in diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        m = MASK_OP.match(line[1:].strip())
        if m and _num(m.group(4)) in (0xFF, 0xFFFF):
            extra_mask = True
            break
    if not extra_mask:
        return []

    out: list[Rewrite] = []
    for m in SOURCE_MASK.finditer(code):
        span = m.span()
        out.append(Rewrite(
            f"drop mask {m.group(1)} at {span[0]}", "mask",
            lambda s, _a=span[0], _b=span[1]: s[:_a] + s[_b:]))
    return out


BRANCH_POLARITY = {"beq": "bne", "bne": "beq",
                   "beqz": "bnez", "bnez": "beqz"}
WHILE_HEAD = re.compile(r"\bwhile\s*\(([^;{}]*)\)\s*\{")


def _matching_brace(code: str, open_at: int) -> int:
    """Index of the '}' closing the '{' at open_at, or -1."""
    depth = 0
    for i in range(open_at, len(code)):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def loop_shape_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Move a loop's test from the top to the bottom, as for(;;) + break.

    NEVER emits a `do` token. The per-function build.sh rejects one outright,
    and this project has already established why that is not the obstacle it
    looks like: the guard bans the TOKEN, not the control-flow shape, and
    build.sh's own error says to use `while` or `for` instead. So
    `for (;;) { body; if (!cond) break; }` is the SANCTIONED form, not a
    workaround -- it compiles to a loop with no entry guard, which is exactly
    the shape a bottom-tested loop needs.

    Recorded in the bank as do-while-functions-are-unmatchable (REFUTED) and
    for-break-rewrite-generalises-across-do-while-sites (CONFIRMED across 389
    of 390 sites). A first version of this generator emitted `do`/`while`
    anyway and was rejected by the build in one compile.

        -bne v1,a0,14      the target closes the loop with a bottom test
        +beq v1,v0,30      we guard it at the top instead

    A top-tested `while` emits an entry guard and a backward jump; a
    bottom-tested `do` emits neither, so the two differ in instruction count
    and in every later branch target. On initMenuAssetHandles -- 13
    instructions, stuck at 83.385 -- that is the whole residual, and it is the
    same fault class that dominates the medium and large tiers where nothing
    has ever matched.

    NOT SEMANTICS-PRESERVING, unlike the padding and mask rewrites: a
    bottom-tested loop runs its body at least once, so this changes behaviour
    when the loop could execute zero times. It is proposed because the original
    source frequently DID know the loop runs at least once, and the oracle
    rejects it when that is wrong. Flagged here because the other generators
    can claim safety and this one cannot.
    """
    # The signal is a SURPLUS conditional branch on our side, not an inverted
    # one. Reading the raw diff suggested `-bne` against `+beq`, but the two
    # streams actually pair bne with bne: the `beq` is an EXTRA line with no
    # counterpart, which is exactly the entry guard a top-tested `while` emits
    # and a `do` does not. Counting branches per side sees that; comparing
    # paired opcodes never can, because the surplus line is unpaired by
    # definition.
    def branches(prefix: str) -> int:
        n = 0
        for line in diff.splitlines():
            if not line.startswith(prefix) or line.startswith(prefix * 3):
                continue
            m = OPCODE.match(line[1:].strip())
            if m and m.group(1) in BRANCH_POLARITY:
                n += 1
        return n

    if branches("+") <= branches("-"):
        return []                       # no surplus guard to remove

    out: list[Rewrite] = []
    for m in WHILE_HEAD.finditer(code):
        open_at = code.index("{", m.start())
        close_at = _matching_brace(code, open_at)
        if close_at < 0:
            continue
        cond = m.group(1).strip()
        body = code[open_at + 1:close_at]

        # A `continue` at THIS loop's level makes the rewrite incorrect, not
        # merely unmatching: in a bottom-tested loop it jumps to the condition
        # test, but in for(;;) it jumps to the top and skips the trailing
        # break, turning a terminating loop into an infinite one. The project
        # measured this across all 390 do-while sites in the game and found
        # exactly one such hazard, so it is rare and real.
        if _has_own_level_continue(body):
            continue

        indent = re.match(r"[ \t]*", code[m.start():]).group(0)
        replacement = (f"for (;;)\n{indent}{{{body}"
                       f"{indent}    if (!({cond})) break;\n{indent}}}")
        old = code[m.start():close_at + 1]
        out.append(Rewrite(f"bottom-test loop on ({cond[:30]})", "loopshape",
                           lambda s, _o=old, _n2=replacement:
                           s.replace(_o, _n2, 1)))
    return out


def _has_own_level_continue(body: str) -> bool:
    """True when `continue` belongs to this loop rather than a nested one."""
    depth = 0
    for m in re.finditer(r"\bfor\b|\bwhile\b|\bcontinue\b|\{|\}", body):
        tok = m.group(0)
        if tok in ("for", "while"):
            depth += 1                  # a nested loop claims the next continue
        elif tok == "}" and depth:
            depth -= 1
        elif tok == "continue" and depth == 0:
            return True
    return False


FRAME_SETUP = re.compile(r"^(?:addiu|subu?)\s+\$?sp,\s*\$?sp,\s*(-?(?:0x)?[0-9a-fA-F]+)")


def frame_padding_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Restore a stack frame IDO shrank, with an unused volatile local.

        -addiu sp,sp,-0x48      the target reserves 0x48
        +addiu sp,sp,-0x20      we reserve 0x20

    On renderRaceUiSingleTrailEffect this restores the stack offsets and moves
    91.519 -> 91.909. It does *not* repair that function's uniform register
    shift: an unused array reserves stack but creates no live compiler web.
    Frame size and register colouring are separate causes there, a distinction
    established by recompiling after this rewrite rather than inferred from
    the original residual.

    The catalogue names the mechanism: an otherwise-unused `volatile u8
    pad[N]` local restores the frame size. `volatile` is what stops IDO
    optimising it away.
    """
    want = got = None
    for line in diff.splitlines():
        if line.startswith(("---", "+++")) or line[:1] not in "+-":
            continue
        m = FRAME_SETUP.match(line[1:].strip())
        if not m:
            continue
        size = abs(_num(m.group(1)) or 0)
        if line[0] == "-" and want is None:
            want = size
        elif line[0] == "+" and got is None:
            got = size
    if want is None or got is None or want <= got:
        return []

    delta = want - got
    body = re.search(r"\)\s*\{", code)
    if not body:
        return []
    at = body.end()
    pad = f"\n    volatile u8 framePad[{delta:#x}];\n"
    return [Rewrite(f"restore frame by {delta:#x} bytes", "frame",
                    lambda s, _at=at, _p=pad: s[:_at] + _p + s[_at:])]


def stack_home_padding_rewrites(code: str, diff: str) -> list[Rewrite]:
    """One measured stack-packing experiment, even when frame sizes agree.

    Three ending callbacks had only matching sw/lw pairs at target 0x18 versus
    candidate 0x1c. A four-byte unused array BEFORE the sole scalar local made
    all three object exact; register spelling and a trailing array did not.
    See catalog `single-local-stack-home-padding`. This is a candidate,
    never proof that padding appeared in the original source.
    """
    target, candidate = diffrepair._streams(diff)
    if not target or len(target) != len(candidate):
        return []
    compact = lambda text: re.sub(r'\s+', ' ', text).strip()
    changes = [(compact(a), compact(b)) for a, b in zip(target, candidate) if compact(a) != compact(b)]
    if not changes:
        return []
    homes, operations = set(), set()
    for a, b in changes:
        ma, mb = MEM.fullmatch(a), MEM.fullmatch(b)
        if (not ma or not mb or ma[1] not in {'lw', 'sw'}
                or ma[1] != mb[1] or ma[2] != mb[2]
                or ma[4].lstrip('$') != 'sp' or mb[4].lstrip('$') != 'sp'
                or not re.fullmatch(r'\$?(?:v[01]|a[0-3]|t[0-9]|s[0-8])', ma[2])):
            return []
        want, got = _num(ma[3]), _num(mb[3])
        if want is None or got is None or not 0x10 <= want <= 0x1000 or want % 8 or got != want + 4:
            return []
        homes.add((want, got))
        operations.add(ma[1])
    if len(homes) != 1 or operations != {'lw', 'sw'}:
        return []
    # Almost every campaign residual is outside this narrow family. Pay for
    # ambiguity-aware alignment only after the inexpensive shape gate passes.
    safe = {(compact(a), compact(b)) for a, b in diffrepair.aligned_pairs(diff)}
    if not set(changes) <= safe:
        return []
    masked = c89._mask(code)
    if re.search(r'\bgd_stack_home_pad\b', masked):
        return []
    # Decline multiple definitions or unfamiliar signatures rather than edit
    # whichever opening brace happens to appear first in a source packet.
    definitions = list(re.finditer(r'\b([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{', masked))
    definitions = [m for m in definitions if m[1] not in {'if', 'for', 'while', 'switch'}]
    if len(definitions) != 1:
        return []
    from solver import principle_variants
    try:
        span = principle_variants._body_span(code, definitions[0][1])
    except ValueError:
        return []
    body = code[span[0]:span[1]]
    prefix, declarations, stop, _ = principle_variants._leading_declarations(body)
    if len(declarations) != 1 or c89._mask(prefix).strip():
        return []
    declaration = declarations[0]
    if (declaration.stars or declaration.initializer
            or ' '.join(declaration.type_text.split()) not in {'s32', 'u32', 'int', 'signed int', 'unsigned int'}
            or re.search(r'&\s*(?:\(\s*)*\b' + re.escape(declaration.name) + r'\b', c89._mask(body[stop:]))):
        return []
    # A separated second declaration is outside the scalar block parser's
    # range. Do not silently treat that as the sole-local measured family.
    tail = c89._mask(body[stop:])
    if re.match(r'\s*(?:[A-Za-z_]\w*\s+)+\**\s*[A-Za-z_]\w*\s*(?:[;=\[])', tail):
        return []
    at = span[0] + len(prefix)
    changed = code[:at] + '    volatile unsigned char gd_stack_home_pad[4];\n' + code[at:]
    want, got = next(iter(homes))
    return [Rewrite(f'pack single stack home {got:#x}->{want:#x} with leading 4-byte pad', 'stack-home',
                    lambda source, old=code, new=changed: new if source == old else source)]


# A statement-order candidate often comes from the semantic repairer, whose
# compact edits preserve several operations on one physical line:
#
#     v1++; t5 = (u16 *)((char *)t5 + 2); v0++;
#
# The original scanner was line-anchored and recognised assignments only, so
# it saw NONE of those three statements.  Formatting had accidentally become
# a search-space gate.  Match conservative, side-effect-free expression
# statements after a line/block/statement boundary instead.  Matching runs on
# c89._mask(), so semicolons and assignment-looking text in comments or string
# literals cannot become rewrite candidates.
_LVALUE = (r"(?:\*+\s*)?[A-Za-z_]\w*"
           r"(?:(?:->|\.)[A-Za-z_]\w*|\[[^\]\n;]+\])*")
STMT = re.compile(
    rf"(?:^|(?<=[;{{}}]))[ \t]*"
    rf"(?P<lhs>{_LVALUE})\s*"
    rf"(?:(?P<update>\+\+|--)|"
    rf"(?P<assign>(?:<<|>>|[+\-*/%&|^])?=)\s*"
    rf"(?P<rhs>[^;{{}}\n]+))\s*;",
    re.M,
)
IDENT = re.compile(r"[A-Za-z_]\w*")


@dataclass(frozen=True)
class _Statement:
    start_pos: int
    end_pos: int
    text: str
    lhs: str
    reads: str

    def start(self) -> int:
        return self.start_pos

    def end(self) -> int:
        return self.end_pos


def _statements(code: str) -> list[_Statement]:
    """Conservative reorderable statements, independent of line wrapping."""
    masked = c89._mask(code)
    out: list[_Statement] = []
    for match in STMT.finditer(masked):
        lhs = match.group("lhs")
        rhs = match.group("rhs") or ""
        # Compound assignments and ++/-- read the value they overwrite.
        reads = rhs
        if match.group("update") or match.group("assign") != "=":
            reads = f"{lhs} {reads}"
        out.append(_Statement(
            match.start(), match.end(), code[match.start():match.end()],
            lhs, reads,
        ))
    return out


_PROLOGUE_DECL = re.compile(
    rf"^(?P<indent>[ \t]*)(?P<type>{c89.TYPE_WORD})"
    rf"(?P<ptr>(?:\s*\*)+\s*|\s+)"
    rf"(?P<name>\w+)\s*(?P<array>\[[^\]]+\])?"
    rf"\s*(?P<rest>=[^;]*)?;[ \t]*$"
)


def _split_prologue_initializers(code: str) -> str:
    """Expose top-of-function initializers as reorderable assignments.

    C89 requires declarations before statements.  Merely teaching the scanner
    to see ``s32 v0 = 0;`` cannot therefore move an independent store before
    that initializer.  Split the whole prologue atomically:

        s32 v0 = 0;  s32 v1 = 1;  *dst = n;
        -> s32 v0;   s32 v1;       v0 = 0; v1 = 1; *dst = n;

    The initializer order is preserved and every assignment remains before
    the function's first executable statement, so this is semantics-preserving
    for the automatic scalar declarations accepted below.  Static/const,
    arrays, aggregates, and shapes we cannot parse make the pass decline.
    """
    masked = c89._mask(code)
    function = re.search(r"\)\s*\{", masked)
    if not function:
        return code

    lines = code.splitlines(keepends=True)
    masked_lines = masked.splitlines(keepends=True)
    body_line = masked.count("\n", 0, function.end() - 1)
    changed: dict[int, str] = {}
    assignments: list[str] = []
    last_decl = None

    for index in range(body_line + 1, len(lines)):
        masked_line = masked_lines[index].rstrip("\r\n")
        if not masked_line.strip():       # blank line or comment
            continue
        match = _PROLOGUE_DECL.match(masked_line)
        if not match:
            break
        last_decl = index
        if not match.group("rest"):
            continue
        type_text = code[
            sum(len(line) for line in lines[:index]) + match.start("type"):
            sum(len(line) for line in lines[:index]) + match.end("type")]
        if not c89._splittable(type_text) or match.group("array"):
            return code

        original = lines[index].rstrip("\r\n")
        ending = lines[index][len(original):]

        def original_group(name: str) -> str:
            start, end = match.span(name)
            return original[start:end] if start >= 0 else ""

        rest = original_group("rest")
        if rest.lstrip().startswith("={") or rest.lstrip().startswith("= {"):
            return code
        semicolon = original.find(";", match.end("rest"))
        if semicolon < 0:
            return code
        tail = original[semicolon + 1:]
        declaration = (original_group("indent") + original_group("type")
                       + original_group("ptr") + original_group("name")
                       + ";" + tail + ending)
        assignment = (original_group("indent") + original_group("name")
                      + " " + rest.strip() + ";" + ending)
        changed[index] = declaration
        assignments.append(assignment)

    if not assignments or last_decl is None:
        return code

    out: list[str] = []
    for index, line in enumerate(lines):
        out.append(changed.get(index, line))
        if index == last_decl:
            out.extend(assignments)
    return "".join(out)


def statement_order_rewrites(code: str, diff: str, *, gate: bool = True,
                             max_variants: int = 40) -> list[Rewrite]:
    """Swap adjacent INDEPENDENT statements to permute register colouring.

    Applies when the instruction streams have the same opcode multiset and the
    remaining differences are allocation-shaped. On
    renderRaceUiSingleTrailEffect, target and candidate both emit 75
    instructions, all 43 differing lines pair, and no opcode count differs.
    That refutes the specific "surplus load to hoist" signal; it does not prove
    that every live range/web is already shaped correctly.

    The measured model says equal-priority webs break ties on web NUMBER, which
    is construction chronology, so source statement order is one evidence-backed
    dial. The reference
    project measured this directly: 24 permutations of four independent
    assignments moved positional word mismatches from 271 to 397 with
    instruction sequence, count, frame size and every stack home unchanged.

    Only INDEPENDENT neighbours are swapped -- neither may read or write what
    the other touches -- which makes the transform semantics-preserving. That
    is what separates it from the frame and hoist ideas, both of which tried to
    add something the residual never showed was missing.

    `gate` decides whether the residual must still look allocation-shaped.
    eval.allocsearch passes gate=False once its FIRST residual has passed the
    test: a swap can introduce a transient structural fault that closes the
    gate and strands the search at depth 2, and the gate exists to choose the
    lever, not to re-choose it at every step of a search already committed.

    A CAVEAT WORTH THE SPACE: the catalogue warns that dist.py's reordering
    penalty HIDES this lever, so the byte score may not rise even when the
    permutation is right. The oracle's exact flag is the only reliable judge
    here, and eval/compose keeps rewrites that score worse for exactly this
    reason.
    """
    if gate and not _allocation_shaped(diff):
        return []

    if max_variants <= 0:
        return []
    out: list[Rewrite] = []
    split = _split_prologue_initializers(code)
    if split != code:
        out.append(Rewrite(
            "split prologue initializers for ordering", "stmtorder",
            lambda source, _old=code, _new=split:
            _new if source == _old else source,
        ))
    groups: list[list[Rewrite]] = []
    for run in _independent_runs(code):
        # Adjacent swaps alone reach a small corner of the permutation space:
        # on renderRaceUiSingleTrailEffect they gave eight candidates and the
        # search exhausted at depth 2 with the residual still one mis-coloured
        # web. Relocating a statement to ANY position in its run reaches the
        # orderings a sequence of adjacent swaps would need many steps to find,
        # and construction chronology -- which is what breaks colouring ties --
        # follows this order directly.
        texts = [m.text for m in run]
        start, end = run[0].start(), run[-1].end()
        indent = re.match(r"[ \t]*", texts[0]).group(0)
        group: list[Rewrite] = []
        moves = []
        for i in range(len(run)):
            for j in range(len(run)):
                if i == j:
                    continue
                destination = j if j < i else j - 1
                if destination == i:
                    continue
                moves.append((abs(destination - i), i, j))
        # Cheap adjacent swaps first, then longer relocations. Candidate groups
        # are interleaved below so one large prologue cannot consume the entire
        # budget and hide a two-statement lever near the residual.
        for _distance, i, j in sorted(moves):
            moved = list(texts)
            item = moved.pop(i)
            moved.insert(j if j < i else j - 1, item)
            if moved == texts:
                continue
            # Normalise only the statements being permuted.  This makes a
            # compact semantic-repair line independently editable while
            # leaving the rest of the candidate byte-for-byte untouched.
            new_block = indent + ("\n" + indent).join(
                statement.strip() for statement in moved)
            group.append(Rewrite(
                f"move statement {run[i].lhs[:14]} "
                f"{i}->{j} at {start}", "stmtorder",
                lambda s, _a=start, _b=end, _n=new_block, _c=code:
                (_c[:_a] + _n + _c[_b:]) if s == _c else s))
        if group:
            groups.append(group)

    tier = 0
    while len(out) < max_variants:
        added = False
        for group in groups:
            if tier < len(group):
                out.append(group[tier])
                added = True
                if len(out) >= max_variants:
                    break
        if not added:
            break
        tier += 1
    return out


def _independent_runs(code: str) -> list[list]:
    """Maximal windows of adjacent, PAIRWISE independent statements.

    Pairwise rather than merely neighbour-wise: relocating a statement past
    several others is only safe if it is independent of every one it crosses.
    Windows may overlap.  If A is independent of B, A conflicts with C, but B
    is independent of C, both [A, B] and [B, C] are valid levers.  Resetting
    the run at C silently lost the second one -- precisely the compact
    ``t9=...; v1++; t5=...; v0++;`` shape that motivated this scanner.
    """
    stmts = _statements(code)
    masked = c89._mask(code)
    runs: list[list] = []
    current: list = []
    previous = None
    for m in stmts:
        adjacent = previous is not None and (
            m.start() == previous.end()
            or not masked[previous.end():m.start()].strip())
        if not adjacent:
            if len(current) > 1:
                runs.append(list(current))
            current = [m]
            previous = m
            continue

        if not all(_independent(x, m) for x in current):
            if len(current) > 1:
                # The sliding window below mutates ``current``.  Store a copy
                # or every earlier run aliases the final suffix and the
                # supposedly expanded search space collapses again.
                runs.append(list(current))
            while current and not all(_independent(x, m) for x in current):
                current.pop(0)
        current.append(m)
        previous = m
    if len(current) > 1:
        runs.append(list(current))
    return runs


def _allocation_shaped(diff: str) -> bool:
    """True when the whole residual is the ALLOCATOR's output, not the C's.

    The first cut of this asked for a pure register residual -- every pair same
    opcode, registers differing. Measured against the function it was written
    for, that rejected a residual which is allocation-shaped in every line:

        [REGS-EQUAL]  -sw v1,0x18(sp)      +sw v1,0x1c(sp)
        [OPCODE]      -addiu t3,t3,%lo(D_2003538)   +lui t0,0x600
        [OPCODE]      -lui   t2,0x600      +addiu t1,t1,%lo(gAssetHandles)

    None of those three is a fault in the C. The first is an SP-RELATIVE slot,
    which is a spill home the allocator chose, not a struct field offset --
    signals.analyse counts it as `layout` because it only looks at the offset
    operand, and a struct repair driven by it would corrupt a struct to chase a
    stack slot. The other two are the same two instructions in the opposite
    ORDER, which is why they face each other as differing opcodes.

    So the test is not purity, it is that nothing outside the allocator's reach
    is wrong: the two streams must hold the SAME MULTISET of opcodes (nothing
    missing, nothing surplus -- an extra web would show up here), every
    differing line must pair, at least one pair must differ in registers, and
    no pair may differ in a non-SP memory offset or an access width, because
    those are genuine layout faults with a generator of their own.
    """
    minus = [l for l in diff.splitlines()
             if l.startswith("-") and not l.startswith("---")]
    plus = [l for l in diff.splitlines()
            if l.startswith("+") and not l.startswith("+++")]
    if not minus or len(minus) != len(plus):
        return False

    # A local scheduler move appears as unpaired delete/add rows even when the
    # complete changed instruction multiset is identical after physical
    # register names are erased. Accept that strongest possible allocation
    # signal before asking the line aligner for one-to-one pairs. This is the
    # exact shape of an increment moving across a branch delay slot.
    from collections import Counter
    def erase(line: str) -> str:
        return ALLOC_REGISTER.sub("REG", line[1:].strip())
    if Counter(erase(line) for line in minus) == \
            Counter(erase(line) for line in plus):
        return True

    pairs, _n, _m = signals._pairs(diff)
    if len(pairs) != len(minus):
        return False                      # something is unpaired

    target, cand = diffrepair._streams(diff)
    if not target or not cand:
        return False
    def ops(stream):
        return Counter(OPCODE.match(x).group(1) for x in stream
                       if OPCODE.match(x))
    # The stated worry is a MISSING or SURPLUS instruction -- "an extra web
    # would show up here" -- and that is a count imbalance, which this tests
    # directly. Strict multiset equality tested something stronger and refused
    # a SUBSTITUTION: on compressRaceRecordReplayData the streams are 18 and
    # 18, seventeen pairs differ only in registers, zero layout faults, and the
    # single remaining pair is `bgtz t1,c4` against `bnez t1,c4`. One branch
    # opcode closed the gate on the purest allocation residual in the tier and
    # propose() returned nothing at all -- the same purity-versus-dominance
    # mistake this docstring already records for layout, left standing on the
    # opcode axis. Substitutions are folded into the dominance test below.
    if sum(ops(target).values()) != sum(ops(cand).values()):
        return False                      # an instruction is missing or extra
    substituted = sum((ops(target) - ops(cand)).values())

    # DOMINANCE, not purity. Requiring zero layout faults meant
    # renderRaceUiSingleTrailEffect -- structural 0 and FORTY-ONE register
    # faults, the purest allocation residual in the tier -- was refused
    # because two offsets were also wrong, and no layout generator fired on
    # those two either. The function ended up with zero proposals of any kind.
    # The lever applies when allocation is the story, not only when it is the
    # whole story; the oracle still judges every proposal.
    registers = layout = 0
    for a, b in pairs:
        ma, mb = MEM.match(a), MEM.match(b)
        if ma and mb:
            on_stack = ma.group(4).lstrip("$") == "sp" \
                and mb.group(4).lstrip("$") == "sp"
            if ma.group(1) != mb.group(1) or (ma.group(3) != mb.group(3)
                                              and not on_stack):
                layout += 1
                continue
        if signals._regs(a) != signals._regs(b):
            registers += 1
    return registers >= 1 and registers >= 2 * (layout + substituted)


# A call is a NAME immediately before an open paren. Testing for a bare "("
# instead treated every CAST as a call -- `gRegionAllocPtr = (u8 *)blk + 8;` is
# the commonest statement in the function this generator was written for, so
# the whole run of them was refused and the search space collapsed to two
# swaps, which is why it exhausted at depth 1.
CALLISH = re.compile(r"\b[A-Za-z_]\w*\s*\(")


def _independent(a: _Statement, b: _Statement) -> bool:
    """Neither statement can observe the other's write.

    Comparing BASE identifiers was too coarse to be useful: `blk->w0 = ...`
    and `blk->w1 = ...` both mention `blk`, so every neighbour in the function
    this was written for came out dependent and the generator proposed nothing.
    They are distinct members of one object and provably do not alias.

    What actually matters is whether either statement's WRITE is visible to the
    other, so compare the assigned lvalues in full and treat a plain-identifier
    write as dangerous only when that identifier appears on the other side.
    Anything containing a call is refused outright -- a call may touch shared
    state that no amount of reading the two statements would reveal.
    """
    la, lb = a.lhs.replace(" ", ""), b.lhs.replace(" ", "")
    if la == lb:
        return False                      # both write the same place
    ta, tb = a.text, b.text
    if CALLISH.search(a.reads) or CALLISH.search(b.reads):
        return False                      # a call: unknowable side effects
    # Full member/array lvalues matter too. The first implementation checked
    # only plain identifiers, so it would swap `blk->w0 = 1` with
    # `x = blk->w0`, changing meaning while calling the pair independent.
    if re.search(re.escape(la), b.reads) \
            or re.search(re.escape(lb), a.reads):
        return False
    # a whole-variable write the other statement reads or overwrites
    if IDENT.fullmatch(la) and re.search(r"(?<![\w.])" + re.escape(la)
                                         + r"(?![\w])", tb):
        return False
    if IDENT.fullmatch(lb) and re.search(r"(?<![\w.])" + re.escape(lb)
                                         + r"(?![\w])", ta):
        return False
    return True


def layout_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Offset, width and ordering repairs, from the existing diffrepair pass."""
    out: list[Rewrite] = []
    repaired, changed, _info = diffrepair.repair(code, diff)
    if changed and repaired != code:
        out.append(Rewrite("diffrepair layout", "layout",
                           lambda s, _r=repaired: _r if s == code else s))
    return out


def global_load_signedness_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Retype only a source-local scalar named by a same-width load residual."""
    types = {'lb':'s8', 'lbu':'u8', 'lh':'s16', 'lhu':'u16'}
    allowed = {('lb','lbu'), ('lbu','lb'), ('lh','lhu'), ('lhu','lh')}
    masked, out, seen = c89._mask(code), [], set()
    for target, candidate in signals._pairs(diff)[0]:
        mt, mc = OPCODE.match(target), OPCODE.match(candidate)
        if not mt or not mc or (mt[1], mc[1]) not in allowed:
            continue
        if target[len(mt[1]):].strip() != candidate[len(mc[1]):].strip():
            continue
        symbol = re.search(r'%lo\(([A-Za-z_]\w*)\)', target)
        if not symbol:
            continue
        declaration = re.compile(r'\bextern\s+('+types[mc[1]]+r')\s+'+re.escape(symbol[1])+r'\s*;')
        matches = list(declaration.finditer(masked))
        if len(matches) != 1:
            continue
        match = matches[0]
        changed = code[:match.start(1)] + types[mt[1]] + code[match.end(1):]
        if changed not in seen:
            seen.add(changed)
            out.append(Rewrite(f'test global load signedness {symbol[1]} -> {types[mt[1]]}',
                'global-load-signedness', lambda source, old=code, new=changed: new if source == old else source))
    return out


def pointer_element_width_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Test narrow indexed pointees when BOTH load width and scale disagree.

    Register-aligned residuals constrain the machine operation, not which C
    declaration caused it. Each source-local extern is a separate hypothesis;
    headers are untouched and the compiler/debugger must adjudicate it.
    """
    pairs = signals._pairs(diff)[0]
    widths = {'lb': ('s8', 0), 'lbu': ('u8', 0),
              'lh': ('s16', 1), 'lhu': ('u16', 1)}
    wanted = set()
    for target, candidate in pairs:
        mt, mc = MEM.match(target), MEM.match(candidate)
        if not mt or not mc:
            continue
        ot, oc = OPCODE.match(target)[1], OPCODE.match(candidate)[1]
        if ot in widths and oc == 'lw' and target[len(ot):].strip() == candidate[len(oc):].strip():
            wanted.add(widths[ot])
    scales = set()
    shift = re.compile(r'^sll\s+([^,]+),([^,]+),\s*(0x[0-9a-fA-F]+|\d+)\s*$')
    for target, candidate in pairs:
        mt, mc = shift.match(target.strip()), shift.match(candidate.strip())
        if mt and mc and mt.groups()[:2] == mc.groups()[:2] and _num(mc[3]) == 2:
            scales.add(_num(mt[3]))
    masked = c89._mask(code)
    declaration = re.compile(r'\bextern\s+(s32|u32)\s*\*\s*([A-Za-z_]\w*)\s*;')
    out = []
    for ctype, scale in sorted(wanted):
        if scale not in scales:
            continue
        for match in declaration.finditer(masked):
            if not re.search(r'\b'+re.escape(match[2])+r'\s*\[', masked):
                continue
            changed = code[:match.start(1)] + ctype + code[match.end(1):]
            out.append(Rewrite(f'test indexed pointee {match[2]} -> {ctype}', 'pointer-element-width',
                lambda source, old=code, new=changed: new if source == old else source))
            if len(out) >= 8:
                return out
    return out


def pointer_difference_scale_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Test removing a second scale from a same-typed pointer difference.

    C already scales pointer subtraction by element size. A surplus candidate
    shift plus an explicit source shift is evidence for testing this edit,
    not proof that the shift is redundant or that these pointers share an object.
    """
    shifts = {}
    for match in re.finditer(r'(?m)^([+-])\s*sra\s+\$?\w+,\s*\$?\w+,\s*(0x[0-9a-fA-F]+|[0-9]+)\s*$', diff):
        amount = _num(match[2])
        shifts[amount] = shifts.get(amount, 0) + (1 if match[1] == '+' else -1)
    masked = c89._mask(code)
    pointers = {}
    declaration = re.compile(r'(?:^|[;{,(])\s*((?:struct\s+)?[A-Za-z_]\w*)\s*\*\s*(\w+)\s*(?=[,;)=])', re.M)
    for match in declaration.finditer(masked):
        pointers.setdefault(match[2], set()).add(match[1])
    pattern = re.compile(r'\(\s*(\w+)\s*-\s*(\w+)\s*\)(\s*>>\s*(0x[0-9a-fA-F]+|[0-9]+))\b')
    out = []
    for match in pattern.finditer(masked):
        types = pointers.get(match[1], set())
        if len(types) != 1 or types != pointers.get(match[2]) or types & {'void','char','s8','u8'}:
            continue
        if not 0 < (_num(match[4]) or 0) < 5 or shifts.get(_num(match[4]), 0) <= 0:
            continue
        changed = code[:match.start(3)] + code[match.end(3):]
        out.append(Rewrite(f'test removing duplicate pointer-difference scale {match[1]} - {match[2]}',
            'pointer-difference-scale', lambda source, old=code, new=changed: new if source == old else source))
        if len(out) >= 8:
            break
    return out


def byte_pointer_step_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Test byte units for typed local increments matching a target byte stride.

    Matching the literal is a correspondence hypothesis, not a type fact. The
    ordinary compiler/semantic gates must evaluate every resulting candidate.
    """
    strides = set()
    for match in re.finditer(r'(?m)^-\s*addiu\s+\$?(\w+),\s*\$?\1,\s*(0x[0-9a-fA-F]+|[0-9]+)\s*$', diff):
        if match[1] not in {'sp', 'gp', 'ra'}:
            value = _num(match[2])
            if value is not None and value > 0:
                strides.add(value)
    masked = c89._mask(code)
    pointers = {}
    for match in re.finditer(r'(?m)^[ \t]+((?:struct\s+)?[A-Za-z_]\w*)\s*\*\s*(\w+)\s*;', masked):
        pointers.setdefault(match[2], set()).add(match[1])
    out = []
    for match in re.finditer(r'(?m)^[ \t]*(\w+)\s*\+=\s*(0x[0-9a-fA-F]+|[1-9][0-9]*)\s*;', masked):
        name, literal = match.groups()
        types = pointers.get(name, set())
        if len(types) != 1 or _num(literal) not in strides:
            continue
        ctype = next(iter(types))
        if ctype in {'char', 's8', 'u8', 'void'}:
            continue
        replacement = f'{name} = ({ctype} *)((unsigned char *){name} + {literal});'
        changed = code[:match.start()] + replacement + code[match.end():]
        out.append(Rewrite(f'test byte-unit pointer step {name} + {literal}', 'pointer-stride',
                           lambda s, result=changed: result if s == code else s))
    return out


def propose(code: str, diff: str) -> list[Rewrite]:
    """Every applicable rewrite for this residual, cheapest kind first."""
    return (prototype_rewrites(code, diff)
            + layout_rewrites(code, diff)
            + shared_layout_rewrites(code, diff)
            + per_object_layout_rewrites(code, diff)
            + pointer_table_deref_rewrites(code, diff)
            + byte_pointer_step_rewrites(code, diff)
            + pointer_element_width_rewrites(code, diff)
            + pointer_difference_scale_rewrites(code, diff)
            + global_load_signedness_rewrites(code, diff)
            + reloc_padding_rewrites(code, diff)
            + reloc_symbol_rewrites(code, diff)
            + drop_mask_rewrites(code, diff)
            + loop_shape_rewrites(code, diff)
            + frame_padding_rewrites(code, diff)
            + stack_home_padding_rewrites(code, diff)
            + statement_order_rewrites(code, diff)
            + compare_swap_rewrites(code, diff)
            + preincrement_lookup_rewrites(code, diff)
            + narrow_increment_type_rewrites(code, diff)
            + materialize_increment_input_rewrites(code, diff)
            + inline_temporary_rewrites(code, diff)
            + branch_sentinel_rewrites(code, diff)
            + signed_compare_rewrites(code, diff)
            + immediate_rewrites(code, diff)
            + argswap_rewrites(code, diff))


def per_object_layout_rewrites(code: str, diff: str) -> list[Rewrite]:
    """One padding proposal per (object, struct) pair the evidence supports.

    layout_rewrites asks diffrepair for THE repair, which requires every offset
    fault in the function to describe one globally consistent struct. Measured
    on the medium tier that almost never holds, and the pass then emits
    nothing: 38, 27 and 25 offset faults on three functions produced zero
    proposals between them.

    This asks instead for every candidate map (see constraint_sets) and pairs
    each with the struct regions it could plausibly describe. A map is offered
    to a region only when EVERY produced offset it constrains is a real field
    offset in that region -- otherwise the proposal is padding a struct the
    evidence never mentioned, which is guessing dressed as repair.

    Several proposals for one residual is the intended outcome. Which object a
    base register names is not stated by the diff, so it is not inferred here;
    each reading costs one compile and the oracle settles it.
    """
    out: list[Rewrite] = []
    regions = diffrepair._struct_regions(code)
    if not regions:
        return out
    seen: set[str] = set()

    # Padding cannot invert two fields, so an order-violating map is not noise
    # -- it says the DECLARED ORDER is wrong. reorder_fields already handles
    # that and was previously reachable only through the global map, which the
    # medium tier never produces.
    for base, mapping in diffrepair.reorder_sets(diff)[:4]:
        new, changed = diffrepair.reorder_fields(code, mapping)
        if changed and new != code and new not in seen:
            seen.add(new)
            out.append(Rewrite(
                f"reorder {len(mapping)} field(s) via {base}", "layout",
                lambda s, _n=new, _c=code: _n if s == _c else s))

    candidates = (diffrepair.constraint_sets(diff)[:8]
                  + diffrepair.delta_clusters(diff)[:6])
    for base, mapping in candidates:
        for region in regions:
            offsets = {off for _m, off, _s in
                       diffrepair.region_fields(code, region)}
            # Restrict the map to offsets this struct actually HAS. Requiring
            # the whole map to fit left 13 good constraints on
            # initRacePlayerLandingSnowSpray unusable, because a base names
            # several objects and no single struct carries all of its offsets.
            # A constraint about a field a struct does not have is not
            # actionable for that struct; it is not evidence against the rest.
            usable = {k: v for k, v in mapping.items() if k in offsets}
            if len(usable) < min(2, len(mapping)):
                continue
            new, changed = diffrepair.apply_constraints_in(code, region,
                                                           usable)
            if not changed or new == code or new in seen:
                continue
            seen.add(new)
            out.append(Rewrite(
                f"pad {len(usable)}/{len(mapping)} field(s) via {base}"
                f" in struct @{region[0]}", "layout",
                lambda s, _n=new, _c=code: _n if s == _c else s))
            if len(out) >= 24:
                return out
    return out


CMP_OPS = {"slt", "sltu", "beq", "bne"}
OPERAND = (r"[A-Za-z_]\w*(?:(?:->|\.)[A-Za-z_]\w*|\[[^\]\[]*\])*"
           r"|0[xX][0-9a-fA-F]+|\d+")
COMPARISON = re.compile(r"(?P<lhs>" + OPERAND + r")\s*"
                        r"(?P<op><=|>=|==|!=|<|>)\s*"
                        r"(?P<rhs>" + OPERAND + r")")
MIRROR = {"<": ">", ">": "<", "<=": ">=", ">=": "<=", "==": "==", "!=": "!="}


def compare_swap_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Comparisons the target evaluates with its operands the other way round.

        -slt at,v1,v0        target compares v1 against v0
        +slt at,v0,v1        we wrote it the other way

        -bne a0,v0,60        same shape on an equality branch
        +bne v0,a0,60

    `a < b` and `b > a` are the same predicate and different instructions --
    MIPS has no `sgt`, so the compiler swaps the operands instead, and which
    register lands on the left is decided by the order they appear in the
    source. That makes operand order a source-level lever with no semantic
    cost, exactly like statement order.

    Seen on two medium functions in one sweep (calculateRaceTimerDelta and
    updateCourseSelectCourseDescription), neither of which had any generator
    that could touch it.

    Mirroring the operator with the operands is what keeps the meaning: `a < b`
    becomes `b > a`, never `b < a`. Equality mirrors to itself.
    """
    swapped = False
    for a, b in signals._pairs(diff)[0]:
        ma, mb = OPCODE.match(a), OPCODE.match(b)
        if not (ma and mb) or ma.group(1) != mb.group(1):
            continue
        if ma.group(1) not in CMP_OPS:
            continue
        ra, rb = signals._regs(a), signals._regs(b)
        # same multiset of registers, different order: an operand swap
        if len(ra) >= 2 and ra != rb and sorted(ra) == sorted(rb):
            swapped = True
            break
    if not swapped:
        return []

    out: list[Rewrite] = []
    seen: set[str] = set()
    for m in COMPARISON.finditer(c89._mask(code)):
        lhs, op, rhs = m.group("lhs"), m.group("op"), m.group("rhs")
        if lhs == rhs:
            continue
        new = f"{rhs} {MIRROR[op]} {lhs}"
        a, b = m.span()
        candidate = code[:a] + new + code[b:]
        if candidate == code or candidate in seen:
            continue
        seen.add(candidate)
        out.append(Rewrite(
            f"swap comparison {lhs} {op} {rhs} at {a}", "cmpswap",
            lambda s, _a=a, _b=b, _n=new, _c=code:
            (_c[:_a] + _n + _c[_b:]) if s == _c else s))
        if len(out) >= 20:
            break
    return out


BRANCH_COMPARE = re.compile(
    r"^(beq|bne)\s+\$?(\w+),\s*\$?(\w+),\s*(\S+)\s*$")
IF_LITERAL_COMPARE = re.compile(
    r"\bif\s*\(\s*"
    r"(?P<left>[A-Za-z_]\w*(?:(?:->|\.)\w+|\[[^\]]+\])*)\s*"
    r"(?P<op>==|!=)\s*"
    r"(?P<literal>-?(?:0[xX][0-9a-fA-F]+|\d+))\s*\)")


def branch_sentinel_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Materialize literals when mirrored C leaves ``beq`` operands swapped.

        -beq a0,v1,48       target keeps the sentinel on the left
        +beq v1,a0,48       candidate compares the loaded value first

    ``compare_swap_rewrites`` first tries the semantics-preserving textual
    mirror.  IDO sometimes canonicalizes that straight back, especially for
    integer literals.  An explicit signed local is the stronger allocation
    lever: ``sentinel = -2; if (sentinel != status)``.

    The transform is bounded to simple equality conditions with at most four
    non-zero literals.  Locals are declared at the function body's start for
    C89, assigned immediately before the enclosing loop or comparison, and
    proposed in both literal orders because declaration order changes IDO web
    numbering.  The oracle checks every proposal.
    """
    swapped = False
    for target, candidate in signals._pairs(diff)[0]:
        mt = BRANCH_COMPARE.match(target)
        mc = BRANCH_COMPARE.match(candidate)
        if not (mt and mc):
            continue
        if mt.group(1) == mc.group(1) and mt.group(4) == mc.group(4) \
                and mt.group(2) == mc.group(3) \
                and mt.group(3) == mc.group(2):
            swapped = True
            break
    if not swapped:
        return []

    masked = c89._mask(code)
    comparisons = []
    for match in IF_LITERAL_COMPARE.finditer(masked):
        value = _num(match.group("literal"))
        if value not in (None, 0):
            comparisons.append(match)
    if not 1 <= len(comparisons) <= 4:
        return []

    literals = list(dict.fromkeys(m.group("literal") for m in comparisons))
    first_if = comparisons[0].start()
    active: list[int] = []
    for pos, char in enumerate(masked[:first_if]):
        if char == "{":
            active.append(pos)
        elif char == "}" and active:
            active.pop()
    if not active:
        return []
    function_open = active[0]

    prefix = masked[function_open + 1:first_if]
    controls = list(re.finditer(r"(?m)^[ \t]*(?:for|while)\b", prefix))
    assign_at = (function_open + 1 + controls[-1].start()
                 if controls else masked.rfind("\n", 0, first_if) + 1)
    line_end = masked.find("\n", assign_at)
    line = masked[assign_at:line_end if line_end >= 0 else len(masked)]
    indent = re.match(r"[ \t]*", line).group(0)
    decl_indent = indent or "    "

    orders = [literals]
    if len(literals) > 1:
        orders.append(list(reversed(literals)))
    out: list[Rewrite] = []
    seen: set[str] = set()
    for order in orders:
        names = {literal: f"branchSentinel{i}"
                 for i, literal in enumerate(order)}
        changed = code
        for match in reversed(comparisons):
            left = code[match.start("left"):match.end("left")]
            replacement = (f"if ({names[match.group('literal')]} "
                           f"{match.group('op')} {left})")
            changed = (changed[:match.start()] + replacement
                       + changed[match.end():])
        assignments = "".join(
            f"{indent}{names[literal]} = {literal};\n" for literal in order)
        changed = changed[:assign_at] + assignments + changed[assign_at:]
        declarations = "\n" + "".join(
            f"{decl_indent}s16 {names[literal]};\n" for literal in order)
        changed = (changed[:function_open + 1] + declarations
                   + changed[function_open + 1:])
        if changed in seen:
            continue
        seen.add(changed)
        out.append(Rewrite(
            "branch sentinels " + ",".join(order), "branch-order",
            lambda source, _old=code, _new=changed:
            _new if source == _old else source))
    return out


TYPED_ARRAY = re.compile(
    r"(?:extern\s+)?(?P<type>[A-Za-z_]\w*)\s+(?P<sym>[A-Za-z_]\w*)\s*[\[;]")


def _element_struct_body(code: str, sym: str) -> str | None:
    """The struct body of the element type of `sym`, however it was declared.

    Two spellings reach the same struct, and only the first was handled:

        struct { ... } gRacePlayers[8];              inline and anonymous
        typedef struct { ... } RacePlayer;           named, then
        extern RacePlayer gRacePlayers[4];           declared separately

    The second is the ordinary way a decompiled header is written, and it is
    what the candidate for updateCourseSelectCourseDescription uses -- so the
    addend repair silently declined on the exact residual it was built for.
    """
    inline = re.search(r"\{([^{}]*)\}\s*" + re.escape(sym) + r"\s*[\[;]", code)
    if inline:
        return inline.group(1)

    type_name = None
    for m in TYPED_ARRAY.finditer(code):
        if m.group("sym") == sym and m.group("type") not in ("struct", "union",
                                                             "return"):
            type_name = m.group("type")
            break
    if not type_name:
        return None
    named = re.search(r"\{([^{}]*)\}\s*" + re.escape(type_name) + r"\s*;",
                      code)
    return named.group(1) if named else None


LOCAL_INIT = re.compile(
    r"^(?P<indent>[ \t]*)(?P<type>[A-Za-z_]\w*)\s+(?P<ptr>\**)\s*"
    r"(?P<name>[A-Za-z_]\w*)\s*=\s*(?P<init>[^;]+);[ \t]*\n", re.M)
ASSIGN_TO = r"(?<![\w.]){}\s*(?:=[^=]|\+\+|--|[-+*/%&|^]=)"

PREINCREMENT_LOOKUP = re.compile(
    r"^(?P<indent>[ \t]*)(?P<type>u8|u16|s16|s32|u32|unsigned\s+char|"
    r"unsigned\s+short|signed\s+short|unsigned\s+int|signed\s+int)"
    r"\s+(?P<temp>[A-Za-z_]\w*)\s*=\s*"
    r"(?P<object>[A-Za-z_]\w*)->(?P<field>[A-Za-z_]\w*)\s*\+\s*1\s*;"
    r"[ \t]*\n(?P=indent)(?P=object)->(?P=field)\s*=\s*(?P=temp)\s*;"
    r"[ \t]*\n(?P=indent)return\s+(?P<table>[A-Za-z_]\w*)\s*\[\s*"
    r"(?P=temp)\s*&\s*(?P<mask>0[xX](?:ff|ffff))\s*\]\s*;",
    re.M | re.I)


def preincrement_lookup_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Collapse an expanded narrow increment-and-lookup into pre-increment.

    The three statements and the compact expression are C-equivalent only
    when the object field and temporary are the same unsigned narrow type.
    Requiring the mask width to agree prevents a plausible-looking rewrite
    from changing wraparound semantics. The allocation-shaped residual gate
    keeps this out of structural mismatches; the oracle still verifies it.
    """
    if not _allocation_shaped(diff):
        return []
    out = []
    for match in PREINCREMENT_LOOKUP.finditer(code):
        width = 1 if match.group("mask").lower() == "0xff" else 2
        temp_type = re.sub(r"\s+", " ", match.group("type").lower())
        if temp_type not in ({"u8", "unsigned char"} if width == 1
                             else {"u16", "unsigned short"}):
            continue
        field_type = (r"(?:u8|unsigned\s+char)" if width == 1
                      else r"(?:u16|unsigned\s+short)")
        if not re.search(
                rf"\b{field_type}\s+{re.escape(match.group('field'))}"
                rf"\s*(?:\[[^\]]+\])?\s*;", code, re.I):
            continue
        replacement = (
            f"{match.group('indent')}return {match.group('table')}"
            f"[++{match.group('object')}->{match.group('field')}];")
        candidate = code[:match.start()] + replacement + code[match.end():]
        out.append(Rewrite(
            f"collapse increment lookup {match.group('temp')}", "inline",
            lambda source, _new=candidate, _old=code:
                _new if source == _old else source))
    return out[:4]


def narrow_increment_type_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Enumerate the promoted temporary type in a masked narrow increment.

    For an unsigned byte/halfword field, ``field + 1`` is already integer-
    promoted. Storing it back truncates, and the matching mask gives the same
    wrapped table index, so retaining it in u8, u16, s32, or u32 is equivalent
    over the field's complete value range. IDO allocates these forms
    differently, making the declaration a codegen choice the binary can
    verify without semantic guessing.
    """
    if not _allocation_shaped(diff):
        return []
    out = []
    for match in PREINCREMENT_LOOKUP.finditer(code):
        current = re.sub(r"\s+", " ", match.group("type").lower())
        width = 1 if match.group("mask").lower() == "0xff" else 2
        field_type = (r"(?:u8|unsigned\s+char)" if width == 1
                      else r"(?:u16|unsigned\s+short)")
        if not re.search(
                rf"\b{field_type}\s+{re.escape(match.group('field'))}"
                rf"\s*(?:\[[^\]]+\])?\s*;", code, re.I):
            continue
        alternatives = ("s32", "u32", "u16", "s16") if width == 1 \
            else ("s32", "u32", "u16", "s16")
        type_start, type_end = match.span("type")
        for replacement in alternatives:
            if replacement == current:
                continue
            candidate = code[:type_start] + replacement + code[type_end:]
            out.append(Rewrite(
                f"increment temporary {match.group('temp')} as {replacement}",
                "type",
                lambda source, _new=candidate, _old=code:
                    _new if source == _old else source))
    return out[:8]


def materialize_increment_input_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Give the pre-increment field value its own web.

    Applicable only after the residual is allocation-shaped and the complete
    increment/store/masked-lookup idiom is present. Reading the unsigned field
    into an unsigned local and using that local in ``+ 1`` is value-equivalent;
    its purpose is to expose the distinct load web visible in the target.
    """
    if not _allocation_shaped(diff):
        return []
    out = []
    for match in PREINCREMENT_LOOKUP.finditer(code):
        width = 1 if match.group("mask").lower() == "0xff" else 2
        field_type = "u8" if width == 1 else "u16"
        field_pattern = (r"(?:u8|unsigned\s+char)" if width == 1
                         else r"(?:u16|unsigned\s+short)")
        if not re.search(
                rf"\b{field_pattern}\s+{re.escape(match.group('field'))}"
                rf"\s*(?:\[[^\]]+\])?\s*;", code, re.I):
            continue
        local = "_increment_input"
        if re.search(rf"\b{local}\b", code):
            continue
        first_statement = (
            f"{match.group('indent')}{field_type} {local} = "
            f"{match.group('object')}->{match.group('field')};\n"
            f"{match.group('indent')}{match.group('type')} "
            f"{match.group('temp')} = {local} + 1;")
        first_end = code.find(";", match.start()) + 1
        if first_end <= match.start():
            continue
        candidate = code[:match.start()] + first_statement + code[first_end:]
        out.append(Rewrite(
            f"materialize increment input for {match.group('temp')}",
            "inline",
            lambda source, _new=candidate, _old=code:
                _new if source == _old else source))
    return out[:4]


def inline_temporary_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Remove one WEB by inlining a local that is read exactly once.

    Every function that plateaus in the fault search ends the same way: the
    residual is pure register allocation and the substitutions are a uniform
    shift up the colour pool -- the target uses a2 where we use a3, t3 where
    we use t4. Under the measured uopt model, colours are handed out lowest
    first to webs ordered by descending save, so a uniform +1 means we carry
    ONE EXTRA WEB ranked ahead of the rest.

    Statement order permutes ties but cannot change how many webs exist, which
    is why it moved renderRaceUiSingleTrailEffect from 41 faults to 34 and then
    stopped. Deleting a named temporary and using its initialiser at the single
    place it is read removes a web outright, which is the other dial.

    Conservative on purpose, because this one rewrites expressions rather than
    moving declarations:

      - exactly one read after the declaration, so the substitution is total
      - no call in the initialiser, whose side effects would move with it
      - nothing the initialiser reads may be assigned between the declaration
        and the read, or the inlined expression sees different values

    A wrong inline still only costs one compile -- the oracle rejects it -- but
    a rewrite that silently changes meaning would poison the search's ranking,
    so it is worth refusing the doubtful cases.
    """
    if not _allocation_shaped(diff):
        return []

    masked = c89._mask(code)
    out: list[Rewrite] = []
    for m in LOCAL_INIT.finditer(masked):
        name, init = m.group("name"), m.group("init").strip()
        if CALLISH.search(init):
            continue
        reads = [u for u in re.finditer(
            r"(?<![\w.])" + re.escape(name) + r"(?![\w])", masked)
            if u.start() >= m.end()]
        if len(reads) != 1:
            continue
        use = reads[0]
        if re.search(ASSIGN_TO.format(re.escape(name)), masked[use.start():]):
            continue                      # the single read is a write
        between = masked[m.end():use.start()]
        inputs = set(IDENT.findall(init)) - {name}
        if any(re.search(ASSIGN_TO.format(re.escape(v)), between)
               for v in inputs):
            continue                      # an input changes before the read
        new = (code[:m.start()] + code[m.end():use.start()]
               + f"({init})" + code[use.end():])
        if new == code:
            continue
        out.append(Rewrite(
            f"inline temporary {name}", "inline",
            lambda s, _n=new, _c=code: _n if s == _c else s))
        if len(out) >= 12:
            break
    return out


def shared_layout_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Place a struct's fields on offsets OTHER functions' residuals pinned.

    Every other layout generator reads only the residual in front of it, so a
    struct is re-derived from scratch in each function and the answer is
    discarded afterwards. RacePlayer is pinned by thirteen different functions
    across fifteen offsets with no width conflict between them; a function
    touching it for the first time can start from that instead of from
    nothing.

    Inert unless solver.layoutstore has been loaded, so importing this changes
    no behaviour -- the store is populated by an experiment that means to use
    it.

    THE PAIRING IS THE HARD PART, AND IT IS KEPT CONSERVATIVE. The store holds
    EXPECTED offsets, which are facts about the ROM; it cannot say which of the
    candidate's declared fields belongs on which of them. Only two readings are
    proposed, both order-preserving:

        equal-count   the region declares exactly as many fields as the store
                      has offsets, so the k-th declared field takes the k-th
                      offset
        prefix        the first min(len) fields take the first min(len)
                      offsets, which is right when the candidate simply stops
                      short of the real struct

    Anything else would be inventing a correspondence, and the oracle checks
    both readings for one compile each.
    """
    from solver import layoutstore

    if not layoutstore.loaded():
        return []
    regions = diffrepair._struct_regions(code)
    if not regions:
        return []
    sizes = diffrepair.type_sizes(code)

    out: list[Rewrite] = []
    seen: set[str] = set()
    for region in regions:
        m = diffrepair.STRUCT_NAME.match(code, region[1])
        if not m:
            continue
        struct_name = m.group("name")
        expected = layoutstore.offsets_for(struct_name)
        if len(expected) < 2:
            continue
        fields = diffrepair.region_fields(code, region, sizes)
        if not fields:
            continue
        produced = [off for _mm, off, _s in fields]

        readings = []
        if len(produced) == len(expected):
            readings.append(("equal-count", dict(zip(produced, expected))))
        k = min(len(produced), len(expected))
        if k >= 2:
            prefix = dict(zip(produced[:k], expected[:k]))
            if not readings or prefix != readings[0][1]:
                readings.append(("prefix", prefix))

        for label, mapping in readings:
            # padding only moves fields later; a map asking for less is not
            # expressible here and belongs to apply_widths
            if any(e < p for p, e in mapping.items()):
                continue
            if diffrepair.order_violation(mapping):
                continue
            new, changed = diffrepair.apply_constraints_in(code, region,
                                                           mapping)
            if not changed or new == code or new in seen:
                continue
            seen.add(new)
            nfuncs = len(layoutstore.functions_for(struct_name))
            out.append(Rewrite(
                f"shared layout {struct_name} ({label}, {len(mapping)} fields"
                f" from {nfuncs} function(s))", "layout",
                lambda s, _n=new, _c=code: _n if s == _c else s))
    return out


CALL_SITE = re.compile(r"(?<![\w.])([A-Za-z_]\w*)\s*\(")
DECLARATION = re.compile(
    r"^[ \t]*(?:extern[ \t]+)?[A-Za-z_][\w \t\*]*?(?<![\w])"
    r"(?P<name>NAME)[ \t]*\([^;{)]*\)[ \t]*;[ \t]*\n", re.M)


def prototype_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Give a caller its callee's VERIFIED signature.

    A callee's prototype decides part of the caller's codegen: an s16 return
    makes the caller sign-extend where u16 does not, and a parameter's declared
    width changes how the argument is marshalled. A candidate that guessed
    `extern void f();` is therefore wrong in the caller's own instructions, and
    no struct or allocation repair can reach it.

    Inert unless solver.protostore is loaded, so importing changes nothing.

    Two proposals per callee, because either spelling can be the one that is
    wrong:

        replace   the caller declares the callee differently -> use the
                  verified declaration instead
        insert    the caller declares it not at all, so the compiler assumes
                  int-returning -> add the verified prototype

    The signature is copied verbatim from source the ORACLE verified byte
    exact. It is not inferred, and where a signature is unknown nothing is
    proposed rather than a guess being made.
    """
    from solver import protostore

    if not protostore.loaded():
        return []

    masked = c89._mask(code)
    called = {m.group(1) for m in CALL_SITE.finditer(masked)}
    out: list[Rewrite] = []
    seen: set[str] = set()

    for name in sorted(called):
        sig = protostore.signature(name)
        if not sig:
            continue
        # never rewrite the function this file DEFINES
        defined = protostore.parse_definition(code, name)
        if defined:
            continue
        rx = re.compile(DECLARATION.pattern.replace("NAME", re.escape(name)),
                        re.M)
        m = rx.search(masked)
        proto = sig["prototype"]
        if m:
            existing = code[m.start():m.end()].strip()
            if existing.rstrip(";") == proto.rstrip(";"):
                continue                  # already correct
            new = code[:m.start()] + proto + "\n" + code[m.end():]
            label = f"prototype {name} -> verified"
        else:
            anchor = code.find("\n", code.find("#include")) + 1
            if anchor <= 0:
                anchor = 0
            new = code[:anchor] + proto + "\n" + code[anchor:]
            label = f"prototype {name} declared"
        if new == code or new in seen:
            continue
        seen.add(new)
        out.append(Rewrite(label, "prototype",
                           lambda s, _n=new, _c=code: _n if s == _c else s))
        if len(out) >= 12:
            break
    return out
