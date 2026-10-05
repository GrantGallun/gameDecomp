"""Attach positions to the rows of an oracle unified diff, for the reader of the diff.

The raw diff strips addresses, so repeated rows (`lb v1,0x10(s0)` three times) cannot be told
apart and nothing ties a row to the C that produced it. Annotation is a trailing comment, never
a prefix, so `-`/`+` stays the first character. It is for prompts and tool output only: edits are
still checked against the raw `attempt.diff`.

  -lb    v1,0(s0)      ; T12
  +lb    v0,0(s0)      ; C12 L34

T/C are line numbers in the target / candidate normalized dumps (from the hunk headers). `L` is
the candidate C line, present only when the compiler's own line records map that instruction and
the opcode agrees; otherwise it is omitted rather than guessed. Target rows have no C source.
"""
from __future__ import annotations

import re

HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _opcode(text: str) -> str:
    parts = text.split(None, 1)
    return parts[0] if parts else ""


def _c_lines(attribution: dict | None) -> dict[int, tuple[int, str]]:
    from solver import source_attribution
    if not attribution or attribution.get("status") != "verified":
        return {}
    out = {}
    for row in source_attribution.instructions_of(attribution):
        if row.get("candidate_line") is not None:
            out[row["normalized_line"]] = (
                row["candidate_line"], _opcode(row.get("instruction", "")))
    return out


def annotate(diff: str, attribution: dict | None = None) -> str:
    """Return `diff` with `; T<n>` / `; C<n> L<m>` appended to every -/+ row."""
    if not diff:
        return diff
    c_lines = _c_lines(attribution)
    target = candidate = None
    out = []
    for line in diff.splitlines():
        hunk = HUNK.match(line)
        if hunk:
            target, candidate = int(hunk[1]), int(hunk[2])
            out.append(line)
        elif target is None or line.startswith(("---", "+++")):
            out.append(line)
        elif line.startswith("-"):
            out.append(f"{line}    ; T{target}")
            target += 1
        elif line.startswith("+"):
            note = f"C{candidate}"
            mapped = c_lines.get(candidate)
            if mapped and mapped[1] == _opcode(line[1:]):
                note += f" L{mapped[0]}"
            out.append(f"{line}    ; {note}")
            candidate += 1
        else:
            out.append(line)
            target += 1
            candidate += 1
    return "\n".join(out) + ("\n" if diff.endswith("\n") else "")
