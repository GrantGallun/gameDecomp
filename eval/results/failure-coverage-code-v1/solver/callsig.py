"""Infer ARGUMENT LOAD WIDTHS at a callee's call sites, program-wide.

NOT the callee's declared signature -- validated against ground truth and that
distinction is real. getRaceCourseSurfaceType is declared (s32, s32, s32), but
every call site loads arg0 with `lh`, a 2-byte load, and passes it
sign-extended. This pass reports s16 because that is what the CALLER does.

For generating matching C that is the more useful fact: the model must emit code
that produces an `lh`, and the declared parameter type is what makes it do so.
But it must not be presented as the callee's prototype, because it is not one.

The user's observation: a choice that is ambiguous inside one function becomes
determined once you see every other function that touches the same symbol.

setCallbackTaskCallback has 585 call sites, addRenderCallback 337. Each site
loads its arguments into a0-a3 immediately before the jal, and the LOAD WIDTH
says what the parameter is: `lh` means s16, `lw` from a struct base means a
4-byte field or pointer, `addiu a1, zero, N` means a small constant. Aggregated
over hundreds of sites, that pins the signature down far harder than any single
function can.

This is evidence, not inference: every fact comes from instructions in the
binary. No source is read, so it is usable on any target including held-out
ones, and it cannot leak ground truth.

Deliberately conservative. A register whose sites disagree is reported as
unknown rather than guessed -- CLAUDE.md invariant 5, unknown is the default.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

ARGS = ("a0", "a1", "a2", "a3")
ADDR_COMMENT = re.compile(r"/\*.*?\*/")
WIDTH_TYPE = {"lb": "s8", "lbu": "u8", "lh": "s16", "lhu": "u16",
              "lw": "s32", "lwc1": "f32", "ldc1": "f64"}


def _clean(line: str) -> str:
    return re.sub(r"\s{2,}", " ", ADDR_COMMENT.sub("", line).strip())


def sites(asm: str, callee: str) -> list[dict[str, str]]:
    """For each `jal callee`, what each argument register was last set from.

    Walks backwards from the call, which is what the delay slot and IDO's
    scheduling require: an argument is frequently set in the delay slot
    immediately AFTER the jal, so that line is included deliberately.
    """
    lines = [_clean(l) for l in asm.splitlines() if l.strip()]
    out = []
    for i, line in enumerate(lines):
        if not re.match(rf"jal\s+{re.escape(callee)}\b", line):
            continue
        found: dict[str, str] = {}
        # the delay slot executes before the call
        window = ([lines[i + 1]] if i + 1 < len(lines) else []) + \
                 list(reversed(lines[max(0, i - 12):i]))
        for w in window:
            m = re.match(r"(\w+)\s+\$?(\w+)\s*,\s*(.*)$", w)
            if not m:
                continue
            op, dst, rest = m.group(1), m.group(2), m.group(3)
            if dst not in ARGS or dst in found:
                continue
            if op in WIDTH_TYPE:
                found[dst] = WIDTH_TYPE[op]
            elif op == "lui" or (op == "addiu" and "%lo" in rest):
                # lui/%lo builds the ADDRESS of a named global
                found[dst] = "pointer"
            elif op in ("move", "or") and re.search(r"\$(a[0-3]|s[0-7]|v[01])",
                                                    rest):
                # copied from another register: a pass-through pointer or value.
                # Reporting this as "unknown" discarded a real constraint --
                # every widely-called utility took mostly these, so every
                # signature came back empty even with 300+ sites read.
                found[dst] = "passthrough"
            elif op == "addiu" and re.search(r"\$zero\s*,\s*-?\d", rest):
                found[dst] = "const"
            elif op in ("addiu", "addu", "ori", "sll", "sra", "andi"):
                found[dst] = "computed"
        if found:
            out.append(found)
    return out


def _asm_files(repo: Path, callee: str, limit: int) -> list[Path]:
    """Every disassembly file containing a call to `callee`.

    Reads repo/asm, the FULL program disassembly (2,306 files), not the ~91
    bootstrapped workspaces. Scanning workspaces found 2 call sites for a
    function with 586 callers, so the aggregation had almost nothing to
    aggregate and reported "nothing confident" for every widely-called symbol.

    Disassembly is derived from the binary, so this is evidence and carries no
    contamination risk -- it is usable on held-out functions too.
    """
    import subprocess
    try:
        out = subprocess.run(
            ["grep", "-rl", f"jal.*{callee}", str(repo / "asm")],
            capture_output=True, text=True, timeout=120)
        return [Path(p) for p in out.stdout.split()][:limit]
    except Exception:
        return []


def signature(repo: Path, callee: str, callers: list[str] | None = None,
              max_callers: int = 200) -> dict:
    """Aggregate argument types for `callee` across many call sites."""
    per_arg: dict[str, Counter] = defaultdict(Counter)
    n_sites = 0
    for d in _asm_files(repo, callee, max_callers):
        try:
            asm = d.read_text(errors="replace")
        except OSError:
            continue
        for site in sites(asm, callee):
            n_sites += 1
            for reg, ty in site.items():
                per_arg[reg][ty] += 1

    result = {"callee": callee, "sites": n_sites, "args": {}}
    for reg in ARGS:
        c = per_arg.get(reg)
        if not c:
            continue
        top, n = c.most_common(1)[0]
        agree = n / sum(c.values())
        # Disagreement means the parameter is polymorphic or our reading is
        # wrong; either way it is unknown, and unknown is the default.
        # "passthrough"/"computed" are real observations, not absences: they
        # say the argument is a pointer or a derived value rather than a narrow
        # scalar. Only genuine disagreement is unknown.
        result["args"][reg] = {
            "type": top if agree >= 0.7 else "unknown",
            "agreement": round(agree, 2),
            "observed": dict(c),
        }
    return result


def render(sig: dict) -> str:
    """A prompt-ready line, or "" when nothing is confidently known."""
    known = [(r, a) for r, a in sig["args"].items() if a["type"] != "unknown"]
    if not known:
        return ""
    parts = [f"{a['type']} arg{i}" for i, (_r, a) in enumerate(known)]
    return (f"{sig['callee']}({', '.join(parts)})   "
            f"/* from {sig['sites']} call sites across the program */")
