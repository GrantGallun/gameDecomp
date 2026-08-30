"""Synthesise struct definitions from the evidence tier.

Measured 2026-08-28: functions stuck at 99%+ fail on struct field OFFSETS, not
register allocation. updateTimeTrialRecordDeltaPopupSlideIn went 99.61 ->
100.00 byte-exact by changing only the struct padding.

The model already receives the correct offsets -- kb_context states
"param0+0x18 2 byte, param0+0x1c 4 byte, param0+0x28 4 byte" -- and uses them
as field NAMES while laying the fields out at 0, 4, 8. It reads the facts and
ignores them.

Seven prompt-level nulls say restating facts does not fix that. So generate the
struct mechanically instead and hand over a definition that cannot be got
wrong. This is deterministic and LLM-free: the offsets come from the binary.

It is also the representation CLAUDE.md already mandates -- "Emit
char unk_00[0x24]; rather than a guessed field" -- which was specified and
never implemented.
"""

from __future__ import annotations

import re

# kind is 'mem_access', not 'access'. The first version of this query used
# 'access', matched zero rows, and reported layout=0 for every function --
# which read as "no struct data available" rather than "the query is wrong".
# A filter that silently matches nothing is indistinguishable from absent data;
# the run below now asserts a non-empty layout before drawing any conclusion.
ACCESSES = """
SELECT base, offset, width, signed, is_load
  FROM evidence
 WHERE kind = 'mem_access'
   AND func_addr = (SELECT addr FROM functions WHERE name = ?)
   AND base != 'unknown'
   AND base NOT LIKE 'stack%'
   AND base NOT LIKE 'global%'
   AND offset >= 0
 ORDER BY base, offset
"""

WIDTH_TYPE = {1: ("s8", "u8"), 2: ("s16", "u16"), 4: ("s32", "u32"),
              8: ("s64", "u64")}


def field_type(width: int, signed) -> str:
    """Unknown signedness defaults to SIGNED.

    Not arbitrary: IDO's plain `lw`/`lh`/`lb` are signed loads, so signed is
    the shape the compiler assumes absent evidence. Guessing unsigned would
    silently change sign-extension codegen.
    """
    s, u = WIDTH_TYPE.get(width, ("s32", "u32"))
    return u if signed == 0 else s


def layout(conn, func: str) -> dict[str, list[tuple[int, int, str]]]:
    """{base: [(offset, width, ctype)]} from observed accesses only.

    Offsets touched by more than one width are a union or a signedness cast;
    the widest access wins, because a narrower field cannot hold a wider one.
    """
    rows = conn.execute(ACCESSES, (func,)).fetchall()
    per_base: dict[str, dict[int, tuple[int, str]]] = {}
    for base, off, width, signed, _is_load in rows:
        if width not in WIDTH_TYPE:
            continue
        slot = per_base.setdefault(base, {})
        prev = slot.get(off)
        if prev is None or width > prev[0]:
            slot[off] = (width, field_type(width, signed))
    return {b: [(o, w, t) for o, (w, t) in sorted(s.items())]
            for b, s in per_base.items()}


def align_positional(body: str, observed: dict[int, int],
                     skip: int = 0) -> tuple[str, bool]:
    """Place the candidate's fields on observed offsets by DECLARATION ORDER.

    repad can only act when a declaration states where it belongs -- a name
    like `field1C`, or a trailing `/* 0x28 */`. Measured across the near-miss
    set, that is exactly what blocks it: feeding repad the whole program's
    global-object layout raised its input from 0 to 186 observed offsets on
    some functions and changed its output on NONE of them, because nothing maps
    a declaration to an offset.

    So propose the mapping instead: the i-th non-padding field goes on the i-th
    observed offset. This is an INFERENCE and it is often wrong -- which is
    fine, because it is never trusted. The oracle scores the result and the
    caller keeps it only if it verifies, which is the same propose/verify shape
    as the argument-order enumeration that moved a function with no model.

    `skip` drops the first N observed offsets, since the candidate may not
    declare the object's leading fields. Widths must agree: mapping an s32 onto
    a byte the program only ever reads as u8 is not a near miss, it is a
    different field, and accepting it would manufacture nonsense the oracle
    then has to reject.

    Index-for-index is a poor model on its own -- it assumes the candidate
    declares EVERY field, and measured across the near-miss set it declined 12
    of 23 functions on width mismatch for exactly that reason (10 declared
    fields against 29 observed offsets). `align_subsequence` is the general
    form; this remains as the cheap exact-arity case.
    """
    decls = [m for m in DECL.finditer(body)
             if not m.group(2).lstrip("_").lower().startswith("pad")]
    if not decls:
        return body, False

    targets = sorted(observed.items())[skip:]
    if len(targets) < len(decls):
        return body, False

    edits: list[tuple[int, int, str]] = []
    cursor = 0
    for m, (off, width) in zip(decls, targets):
        ctype = m.group(1).strip().split()[-1]
        count = m.group(3)
        size = SIZEOF.get(ctype, 4) * (int(count, 0) if count else 1)
        if SIZEOF.get(ctype, 4) != width:
            return body, False              # different field, not a shifted one
        if off < cursor:
            return body, False              # cannot move a field backwards
        if off > cursor:
            indent = re.match(r"[ \t]*", body[m.start():m.end()]).group(0)
            edits.append((m.start(), m.start(),
                          f"{indent}char pad{cursor:02x}[{off - cursor:#x}];\n"))
        cursor = off + size

    if not edits:
        return body, False
    out = body
    for start, end, text in reversed(edits):
        out = out[:start] + text + out[end:]
    return out, True


def align_subsequence(body: str, observed: dict[int, int],
                      greedy: str = "first") -> tuple[str, bool]:
    """Place declared fields on a WIDTH-COMPATIBLE, order-preserving subsequence.

    The candidate declares only the fields its function touches -- 10
    declarations against 29 observed offsets is normal -- so field i is not
    offset i. What must hold is weaker and truer: the declarations appear in
    increasing offset order, and each sits on an offset the program reads at
    that width.

    `greedy="first"` takes the earliest such assignment, `"last"` the latest.
    Two proposals, one compile each, and the oracle picks. Neither is trusted:
    this is a hypothesis about which bytes the candidate means, and a wrong
    guess simply fails to verify.
    """
    decls = [m for m in DECL.finditer(body)
             if not m.group(2).lstrip("_").lower().startswith("pad")]
    if not decls:
        return body, False

    targets = sorted(observed.items())
    if greedy == "last":
        targets = targets[::-1]

    chosen: list[tuple[int, int]] = []
    ti = 0
    for m in decls:
        ctype = m.group(1).strip().split()[-1]
        want = SIZEOF.get(ctype, 4)
        while ti < len(targets) and targets[ti][1] != want:
            ti += 1
        if ti >= len(targets):
            return body, False              # no compatible offset remains
        chosen.append(targets[ti])
        ti += 1
    if greedy == "last":
        chosen.reverse()
        if any(a[0] >= b[0] for a, b in zip(chosen, chosen[1:])):
            return body, False              # not strictly increasing

    edits: list[tuple[int, str]] = []
    cursor = 0
    for m, (off, _w) in zip(decls, chosen):
        ctype = m.group(1).strip().split()[-1]
        count = m.group(3)
        size = SIZEOF.get(ctype, 4) * (int(count, 0) if count else 1)
        if off < cursor:
            return body, False
        if off > cursor:
            indent = re.match(r"[ \t]*", body[m.start():m.end()]).group(0)
            edits.append((m.start(),
                          f"{indent}char pad{cursor:02x}[{off - cursor:#x}];\n"))
        cursor = off + size

    if not edits:
        return body, False
    out = body
    for start, text in reversed(edits):
        out = out[:start] + text + out[start:]
    return out, True


def render(name: str, fields: list[tuple[int, int, str]],
           keep: dict[int, str] | None = None) -> str:
    """Emit a struct where every observed offset lands exactly where observed.

    Gaps become explicit padding rather than being closed up -- closing them is
    precisely the bug this exists to prevent, and an unobserved byte is unknown,
    not absent.
    """
    out = [f"typedef struct {{"]
    cursor = 0
    for off, width, ctype in fields:
        if off > cursor:
            out.append(f"    char pad{cursor:02x}[{off - cursor:#x}];")
        elif off < cursor:
            continue                      # overlapping access, already covered
        fname = (keep or {}).get(off, f"field_{off:02x}")
        out.append(f"    {ctype} {fname};")
        cursor = off + width
    out.append(f"}} {name};")
    return "\n".join(out)


# Built by concatenation, not .format(): the pattern contains literal { and }
# which str.format treats as placeholders, and it raised IndexError rather than
# producing a wrong pattern -- a loud failure, which is the good case.
def _struct_pattern(struct_name: str) -> re.Pattern:
    return re.compile(r"typedef\s+struct\s*(?:\w+\s*)?\{[^{}]*\}\s*"
                      + re.escape(struct_name) + r"\s*;", re.S)


# The declared name is the identifier immediately before the (optional) array
# subscript and the semicolon. An earlier line-anchored pattern that tried to
# skip the type prefix matched nothing at all and silently returned {}, which
# looked like "no names to preserve" rather than "the regex is wrong".
EXISTING_FIELD = re.compile(r"\b(\w+)\s*(?:\[[^\]]*\])?\s*;")


def preserve_names(old_body: str, fields: list[tuple[int, int, str]]
                   ) -> dict[int, str]:
    """Map observed offsets onto the candidate's OWN field names.

    Renaming fields breaks the function body, which still refers to the old
    names -- and the resulting compile failure is easy to misread. On
    updateTimeTrialRecordDeltaPopupSlideOut the rewrite renamed field28 to
    field_28, the candidate stopped compiling, and the harness reported it as
    "no improvement" rather than "broken", hiding a fix that was one padding
    byte away from byte-exact.

    Names that already encode their offset (field1C, unk_28, field_0x18) are
    matched to that offset; anything else is left to the generated name.
    """
    observed = {o for o, _, _ in fields}
    names: dict[int, str] = {}
    for m in EXISTING_FIELD.finditer(old_body):
        ident = m.group(1)
        if ident.lstrip("_").lower().startswith("pad"):
            continue          # padding is regenerated, never preserved

        # Splitting a name into prefix+offset is ambiguous, because letters
        # a-f are hex digits: "field1C" has trailing hex runs C, 1C, d1C,
        # ed1C. A word-boundary anchor cannot help -- there is no boundary
        # inside an identifier. So generate every trailing-hex reading and let
        # the OBSERVED offsets disambiguate; exactly one will normally match.
        tail = ""
        for ch in reversed(ident):
            if ch in "0123456789abcdefABCDEF":
                tail = ch + tail
            else:
                break
        for i in range(len(tail)):
            try:
                off = int(tail[i:], 16)
            except ValueError:
                continue
            if off in observed and off not in names:
                names[off] = ident
                break
    return names


def rewrite(code: str, struct_name: str, fields: list[tuple[int, int, str]]
            ) -> tuple[str, bool]:
    """Replace one struct definition in `code` with the synthesised layout.

    Whitespace-tolerant: an exact-text match already failed once on a stray
    space after `struct`. Returns (code, changed) so a silent no-op is
    detectable -- this project has shipped "patched" edits that never applied.
    """
    pat = _struct_pattern(struct_name)
    m = pat.search(code)
    if not m:
        return code, False
    keep = preserve_names(m.group(0), fields)
    return pat.sub(render(struct_name, fields, keep), code, count=1), True


DECL = re.compile(r"^\s*([A-Za-z_][\w ]*?)\s+([A-Za-z_]\w*)\s*"
                  r"(?:\[\s*(0[xX][0-9A-Fa-f]+|\d+)\s*\])?\s*;", re.M)
SIZEOF = {"u8": 1, "s8": 1, "char": 1, "u16": 2, "s16": 2, "short": 2,
          "u32": 4, "s32": 4, "int": 4, "long": 4, "float": 4, "f32": 4,
          "u64": 8, "s64": 8, "f64": 8, "double": 8}


# A trailing comment on a field declaration, up to the end of that line.
TRAILING_COMMENT = re.compile(r"[ \t]*(?:/\*(.*?)\*/|//([^\n]*))")
# The first hex offset mentioned in it. `/* 0x1A - 0x1B : padding */` names a
# RANGE, so only the first number is the field's own offset.
COMMENT_OFFSET = re.compile(r"0[xX]([0-9A-Fa-f]+)")


def _offset_from_comment(body: str, after: int,
                         observed: dict[int, int]) -> int | None:
    """Offset a field's trailing comment claims for it, if the evidence agrees.

    Returns None unless the claimed offset is one the binary actually observes.
    A comment is the model's assertion, not a fact; requiring it to match the
    evidence tier keeps this on the right side of the evidence/inference line.
    """
    m = TRAILING_COMMENT.match(body, after)
    if not m:
        return None
    text = m.group(1) or m.group(2) or ""
    hit = COMMENT_OFFSET.search(text)
    if not hit:
        return None
    try:
        claimed = int(hit.group(1), 16)
    except ValueError:
        return None
    return claimed if claimed in observed else None


def repad(body: str, observed: dict[int, int]) -> tuple[str, bool]:
    """Resize padding arrays so named fields land on their observed offsets.

    Regenerating a struct from one function's evidence DELETES every field that
    function does not touch -- on updateEndingLindaExitUntilPhase3C that removed
    posX/posY/posZ/rotY/textureId/paletteId and the body stopped compiling. A
    struct is a program-wide fact; per-function evidence cannot reconstruct one.

    So never remove a field. Walk the declarations, track the running offset,
    and when a field whose NAME encodes an offset lands in the wrong place,
    resize the padding array immediately before it. That is precisely the
    one-number fix that closed SlideOut: _pad1[0x4] -> _pad1[0x8].

    `observed` maps offset -> width, from the evidence tier.
    """
    decls = list(DECL.finditer(body))
    if not decls:
        return body, False

    out, cursor, changed = body, 0, False
    edits: list[tuple[int, int, str]] = []       # (start, end, replacement)
    last_pad: tuple[int, int, int] | None = None  # (start, end, cur_size)

    for m in decls:
        ctype, name, count = m.group(1).strip(), m.group(2), m.group(3)
        n = int(count, 0) if count else 1
        size = SIZEOF.get(ctype.split()[-1], 4) * n

        is_pad = name.lstrip("_").lower().startswith("pad")
        if is_pad:
            last_pad = (m.start(), m.end(), size)
            cursor += size
            continue

        want = None
        tail = ""
        for ch in reversed(name):
            if ch in "0123456789abcdefABCDEF":
                tail = ch + tail
            else:
                break
        for i in range(len(tail)):
            try:
                cand = int(tail[i:], 16)
            except ValueError:
                continue
            if cand in observed:
                want = cand
                break

        if want is None:
            # The offset may be declared in a trailing COMMENT rather than in
            # the name. Models routinely write a semantic name and annotate it:
            #
            #     s32 velocity;   /* 0x28 : velocity (signed) */
            #
            # On updateTimeTrialRecordDeltaPopupSlideIn that field actually
            # landed at 0x20, and repad reported "no change" because
            # `velocity` encodes no offset -- so a 99.999 candidate whose only
            # fault was 8 missing bytes of padding could not be repaired.
            #
            # This reads nothing external: the comment is the candidate's OWN
            # statement of where the field belongs, so acting on it resolves an
            # internal contradiction rather than importing an assumption. Still
            # required to agree with the evidence tier before it is used.
            want = _offset_from_comment(body, m.end(), observed)

        if want is not None and want != cursor:
            delta = want - cursor
            if last_pad is not None:
                # resize the padding that is already there
                new_size = last_pad[2] + delta
                if new_size > 0:
                    old = body[last_pad[0]:last_pad[1]]
                    new = re.sub(r"\[\s*(?:0[xX][0-9A-Fa-f]+|\d+)\s*\]",
                                 f"[{new_size:#x}]", old)
                    if new != old:
                        edits.append((last_pad[0], last_pad[1], new))
                        changed = True
                        cursor += delta
            elif delta > 0:
                # INSERT padding where there is none. Prefilled candidates
                # write "s32 field1C; s32 field24; s16 field502;" with no pad
                # fields at all, so those land at 0, 4, 8 instead of 0x1C,
                # 0x24, 0x502. Resizing cannot fix that -- there is nothing to
                # resize -- and an earlier version of this function silently
                # reported "padding already right" for exactly that case.
                indent = re.match(r"[ \t]*", body[m.start():m.end()]).group(0)
                pad = f"{indent}char pad{cursor:02x}[{delta:#x}];\n"
                edits.append((m.start(), m.start(), pad + body[m.start():m.start()]))
                changed = True
                cursor += delta
        cursor += size
        last_pad = None

    for start, end, new in reversed(edits):
        out = out[:start] + new + out[end:]
    return out, changed


def struct_names(code: str) -> list[str]:
    """Names of typedef'd structs defined in the candidate.

    The first version matched r"\\}\\s*(\\w+)\\s*;" and returned ['break'] --
    because \\s* spans newlines, so an ordinary

        }
        break;

    reads as a struct named `break`. That made three functions look like
    "struct rewrite applied and did not help" when in fact they declare no
    structs at all, and the two cases are not distinguishable in the output.

    Anchored to an actual typedef struct body instead.
    """
    return re.findall(r"typedef\s+struct\s*(?:\w+\s*)?\{[^{}]*\}\s*(\w+)\s*;",
                      code, re.S)
