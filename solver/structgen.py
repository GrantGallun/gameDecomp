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

        if want is not None and want != cursor and last_pad is not None:
            delta = want - cursor
            new_size = last_pad[2] + delta
            if new_size > 0:
                old = body[last_pad[0]:last_pad[1]]
                new = re.sub(r"\[\s*(?:0[xX][0-9A-Fa-f]+|\d+)\s*\]",
                             f"[{new_size:#x}]", old)
                if new != old:
                    edits.append((last_pad[0], last_pad[1], new))
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
