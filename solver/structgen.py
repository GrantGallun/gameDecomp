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

ACCESSES = """
SELECT base, offset, width, signed, is_load
  FROM evidence
 WHERE kind = 'access'
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


def render(name: str, fields: list[tuple[int, int, str]]) -> str:
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
        out.append(f"    {ctype} field_{off:02x};")
        cursor = off + width
    out.append(f"}} {name};")
    return "\n".join(out)


STRUCT_RE_TMPL = r"typedef\s+struct\s*(?:\w+\s*)?\{[^{}]*\}\s*{name}\s*;"


def rewrite(code: str, struct_name: str, fields: list[tuple[int, int, str]]
            ) -> tuple[str, bool]:
    """Replace one struct definition in `code` with the synthesised layout.

    Whitespace-tolerant: an exact-text match already failed once on a stray
    space after `struct`. Returns (code, changed) so a silent no-op is
    detectable -- this project has shipped "patched" edits that never applied.
    """
    pat = re.compile(STRUCT_RE_TMPL.format(name=re.escape(struct_name)), re.S)
    if not pat.search(code):
        return code, False
    return pat.sub(render(struct_name, fields), code, count=1), True


def struct_names(code: str) -> list[str]:
    """Names of typedef'd structs defined in the candidate."""
    return re.findall(r"\}\s*(\w+)\s*;", code)
