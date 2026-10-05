"""Put the value the compiler diff states at the source line the compiler attributes it to.

Derived, not hand-written for one function: exploring each residual class's potential (information the diff
carries about the fix vs how often any mechanism converted it) located these classes, and one rule covers them.
With four hand-written mechanisms hidden it rewrote `global_load_signedness` source-for-source on 5 of 5 of
its successes; run forward on the 216 functions the population searches left unsolved it improved 72 of 159
candidates (45%, base rate 5%) in 37 functions (eval/results/retrodiction-20260922/).

Each aligned target/candidate instruction pair (solver.alignment, ambiguity-aware) is classified by what
differs; the source line comes from `source_attribution` (the compiler's own line records), used only when it
was produced by this exact source. Per class:
  field:offset / field:immediate   a literal on that line equal to the candidate's value -> the target's
  field:symbol                     the candidate's relocation symbol on that line -> the target's
  opcode:<load/store pair>         C type of the access (lb s8, lbu u8, lh s16, lhu u16, lw s32): the type token
                                   on that line, else the declaration of an identifier used on it
  extra:<op>                       an instruction the target lacks: its C operator with the same immediate on
                                   that line is removed (andi `& k`, sll `<< k`, sra/srl `>> k`, addiu `+ k`),
                                   or the cast it spells (`sll`/`sra` 16 or 24 = `(s16)`/`(s8)`,
                                   `andi 0xffff`/`0xff` = `(u16)`/`(u8)`)
Register, branch-target and missing-instruction differences carry no C-expressible value and are not handled:
they need an allocator or layout model, which this is not.
"""
from __future__ import annotations

import re

CAP = 16
CTYPE = {"lb": "s8", "lbu": "u8", "lh": "s16", "lhu": "u16", "lw": "s32", "sb": "s8", "sh": "s16", "sw": "s32"}
LOADSTORE = set(CTYPE) | {"lwc1", "swc1"}
OPERATOR = {"andi": "&", "sll": "<<", "sra": ">>", "srl": ">>", "addiu": "+"}
CAST = {("sll", 16): "s16", ("sra", 16): "s16", ("sll", 24): "s8", ("sra", 24): "s8",
        ("andi", 0xFFFF): "u16", ("andi", 0xFF): "u8"}
REG = re.compile(r"^\$?(zero|at|v[01]|a[0-3]|t[0-9]|s[0-8]|k[01]|gp|sp|fp|ra|f\d+)$")
MEMOP = re.compile(r"^(?P<off>[^()]*)\((?P<base>\$?\w+)\)$")
RELOC = re.compile(r"%(?:hi|lo)\((?P<sym>[A-Za-z_]\w*)")
# The same with the addend: `%lo(gActiveGameTaskList+4)` and `%lo(gActiveGameTaskList)` used to compare equal, so a
# symbol-addend residual (resumeGameTask 2026-09-24) produced no class and no proposal: a silent decline.
RELOC_ADDEND = re.compile(r"%(?:hi|lo)\((?P<sym>[A-Za-z_]\w*)(?P<add>[+-](?:0x[0-9A-Fa-f]+|\d+))?\)")
BRANCH = re.compile(r"^(b\w*|j|jal)$")


def _fields(op: str, operands) -> list[tuple[str, str]]:
    out = []
    for o in operands:
        m = MEMOP.match(o)
        if m:
            sym = RELOC_ADDEND.search(m["off"])
            out += [("offset", sym.group("sym") + (sym.group("add") or "") if sym else m["off"]), ("register", m["base"])]
        elif RELOC_ADDEND.search(o):
            sym = RELOC_ADDEND.search(o)
            out.append(("symbol", sym.group("sym") + (sym.group("add") or "")))
        elif REG.match(o):
            out.append(("register", o))
        elif BRANCH.match(op):
            out.append(("branch", o))
        else:
            out.append(("immediate", o))
    return out


def _candidate_stream_lines(diff: str) -> list[int]:
    """Candidate-stream index (as solver.alignment.streams builds it) -> normalized-dump line number."""
    out, line = [], 0
    for raw in diff.splitlines():
        header = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)", raw)
        if header:
            line = int(header.group(1))
            continue
        if not raw or raw.startswith(("---", "+++")):
            continue
        if raw[0] in " +":
            if raw[1:].strip():
                out.append(line)
            line += 1
    return out


def sites(diff: str, attribution: dict | None) -> list[tuple[str, int | None, str | None, str | None]]:
    """(signature, source line or None, target text, candidate text) for every C-expressible residual."""
    from solver import alignment
    from solver.source_attribution import instructions_of
    lines = _candidate_stream_lines(diff)
    where = {r["normalized_line"]: r.get("candidate_line") for r in instructions_of(attribution)} \
        if (attribution or {}).get("status") == "verified" else {}

    def locate(insn):
        return where.get(lines[insn.index]) if insn is not None and insn.index < len(lines) else None

    out = []
    for step in alignment.align_diff(diff).steps:
        t, c = step.target, step.candidate
        if step.ambiguous or c is None or (t is not None and t.text == c.text):
            continue
        if t is None:
            out.append((f"extra:{c.opcode}", locate(c), None, c.text))
        elif t.opcode != c.opcode:
            if t.opcode in LOADSTORE and c.opcode in LOADSTORE:
                out.append((f"opcode:{'/'.join(sorted((t.opcode, c.opcode)))}", locate(c), t.text, c.text))
        else:
            tf, cf = _fields(t.opcode, t.operands), _fields(c.opcode, c.operands)
            differ = {k for (k, a), (_k, b) in zip(tf, cf) if a != b} if len(tf) == len(cf) else {"shape"}
            if len(differ) == 1 and (kind := differ.pop()) in ("offset", "immediate", "symbol"):
                out.append((f"field:{kind}", locate(c), t.text, c.text))
    return out


def _int(text):
    try:
        return int(text, 0)
    except (TypeError, ValueError):
        return None


def _last(text):
    return text.split(",")[-1].strip() if text and "," in text else None


def _forms(value: int) -> set[str]:
    return {f"0x{value:X}", f"0x{value:x}", str(value)} if value >= 0 else {str(value), f"-0x{-value:x}", f"-0x{-value:X}"}


def _on_line(source: str, line: int, pattern: str, repl: str) -> str | None:
    from solver import c89
    lines, masked = source.split("\n"), c89._mask(source).split("\n")
    if not 1 <= line <= len(lines):
        return None
    hit = re.search(pattern, masked[line - 1])          # located in code, never in a comment or string
    if not hit:
        return None
    text = lines[line - 1]
    lines[line - 1] = text[:hit.start()] + re.sub(pattern, repl, text[hit.start():hit.end()], count=1) + text[hit.end():]
    return "\n".join(lines)


def _edits(source: str, sig: str, line: int, target: str | None, cand: str) -> list[str]:
    kind, _, what = sig.partition(":")
    out = []
    if sig in ("field:offset", "field:symbol") and (edits := _symbol_addend(source, line, target, cand)) is not None:
        return edits
    if sig in ("field:offset", "field:immediate"):
        grab = (lambda t: re.search(r"(-?(?:0x)?[0-9a-fA-F]+)\(", t or "")) if sig == "field:offset" else (lambda t: None)
        tv = _int(grab(target).group(1)) if grab(target) else _int(_last(target))
        cv = _int(grab(cand).group(1)) if grab(cand) else _int(_last(cand))
        if tv is None or cv is None:
            return []
        for form in _forms(cv):
            hexa = form.lower().lstrip("-").startswith("0x")
            new = (f"0x{tv:X}" if tv >= 0 else f"-0x{-tv:X}") if hexa else str(tv)
            if (s := _on_line(source, line, rf"(?<![\w.]){re.escape(form)}(?![\w.])", new)):
                out.append(s)
        if not out and sig == "field:offset" and tv >= 0:
            out.extend(_member_to_offset(source, line, cand.split()[0], tv))
    elif sig == "field:symbol":
        ts, cs = RELOC.search(target or ""), RELOC.search(cand or "")
        if ts and cs and (s := _on_line(source, line, rf"\b{re.escape(cs.group('sym'))}\b", ts.group("sym"))):
            out.append(s)
    elif kind == "opcode":
        want, have = CTYPE.get(target.split()[0]), CTYPE.get(cand.split()[0])
        if want and have and want != have:
            if (s := _on_line(source, line, rf"\b{have}\b", want)):
                out.append(s)
            else:
                used = set(re.findall(r"\b[A-Za-z_]\w*\b", source.split("\n")[line - 1]))
                for name in sorted(used):
                    m = re.search(rf"\b{have}(\s*\*?\s*){re.escape(name)}\b", source)
                    if m:
                        out.append(source[:m.start()] + want + m.group(1) + name + source[m.end():])
            if not out:
                # The access is a member of a type the source does not declare (a header): read or write it as
                # the target's width at the same offset (uopt53-member-offset-equivalence).
                offset = re.search(r"(-?(?:0x)?[0-9a-fA-F]+)\(", cand)
                if offset and _int(offset.group(1)) is not None and _int(offset.group(1)) >= 0:
                    out.extend(_member_to_offset(source, line, target.split()[0], _int(offset.group(1))))
    elif kind == "extra":
        imm = _int(_last(cand))
        if what in OPERATOR and imm is not None:
            for form in _forms(imm):
                if (s := _on_line(source, line, rf"\s*{re.escape(OPERATOR[what])}\s*{re.escape(form)}(?![\w.])", "")):
                    out.append(s)
        if (ctype := CAST.get((what, imm))) and (s := _on_line(source, line, rf"\(\s*{ctype}\s*\)\s*", "")):
            out.append(s)
        if not out and ctype:
            # No operator or cast on the line: the mask/extension comes from the assigned local's declared width
            # (H13, partial: a computed value assigned to a u8/u16 local gets `andi`, to s8/s16 `sll`/`sra`;
            # eval/results/mask-type-20260923). Retype that one local to s32.
            assigned = re.match(r"^\s*([A-Za-z_]\w*)\s*(?:[-+*/%|&^]|<<|>>)?=(?!=)", source.split("\n")[line - 1])
            if assigned:
                name = assigned.group(1)
                decl = re.search(rf"(?m)^([ \t]*){ctype}([ \t]+){re.escape(name)}\b", source)
                if decl:
                    out.append(source[:decl.start()] + decl.group(1) + "s32" + decl.group(2) + source[decl.end() - len(name):])
    return out


ACCESS_TYPE = {"lw": "s32", "sw": "s32", "lh": "s16", "sh": "s16", "lhu": "u16", "lb": "s8", "sb": "s8",
               "lbu": "u8", "lwc1": "f32", "swc1": "f32"}
MEMBER = re.compile(r"(?P<base>[A-Za-z_]\w*(?:\[[^\]\n]*\])?(?:(?:->|\.)[A-Za-z_]\w*(?:\[[^\]\n]*\])?)*)"
                    r"(?P<op>->|\.)(?P<m>[A-Za-z_]\w*)(?![\w(])")


def _symbol_addend(source: str, line: int, target: str | None, cand: str) -> list[str] | None:
    """`%lo(SYM+N)` in the target where the candidate reads `%lo(SYM+M)`: read SYM at the target's addend.

    A bare `SYM` on the line becomes `(*(T *)((u8 *)&SYM + N))` (T from the access width, and `void *` for words, since
    the value is often a pointer); a member access `SYM.m` goes through _member_to_offset. None when the two lines are
    not a same-symbol addend difference (the caller then tries the numeric offset rules)."""
    ta, ca = RELOC_ADDEND.search(target or ""), RELOC_ADDEND.search(cand or "")
    if not ta or not ca or ta.group("sym") != ca.group("sym") or ta.group("add") == ca.group("add"):
        return None
    want = _int(ta.group("add") or "0")
    opcode = cand.split()[0]
    if want is None or want < 0 or opcode not in ACCESS_TYPE:
        return []
    from solver import c89
    sym = ta.group("sym")
    text, masked = source.split("\n")[line - 1], c89._mask(source).split("\n")[line - 1]
    byte = "u8" if re.search(r"\bu8\b|common\.h", source) else "unsigned char"
    out = []
    if not ca.group("add"):
        for ctype in [ACCESS_TYPE[opcode]] + (["void *"] if opcode in ("lw", "sw") else []):
            hit = re.search(rf"(?<![&\w.>]){re.escape(sym)}\b(?!\s*(?:\.|\[|->|\())", masked)
            if hit:
                repl = f"(*({ctype} *)(({byte} *)&{sym} + 0x{want:X}))"
                lines = source.split("\n")
                lines[line - 1] = text[:hit.start()] + repl + text[hit.end():]
                out.append("\n".join(lines))
    out.extend(_member_to_offset(source, line, opcode, want))
    return out


def _member_to_offset(source: str, line: int, opcode: str, target: int) -> list[str]:
    """The stated offset where the source spells the access as a member (often of a header type it cannot
    edit): `base->m` / `base.m` -> `*(T *)((u8 *)base + TARGET)`, T from the access opcode. IDO 5.3 compiles
    the two spellings identically (catalog uopt53-member-offset-equivalence: 30 of 30 paired compiles)."""
    from solver import c89
    ctype = ACCESS_TYPE.get(opcode)
    if not ctype:
        return []
    text, masked = source.split("\n")[line - 1], c89._mask(source).split("\n")[line - 1]
    byte = "u8" if re.search(r"\bu8\b|common\.h", source) else "unsigned char"
    out = []
    for m in MEMBER.finditer(masked):
        base = text[m.start("base"):m.end("base")]
        address = base if m.group("op") == "->" else f"&{base}"
        repl = f"(*({ctype} *)(({byte} *)({address}) + 0x{target:X}))"
        new_line = text[:m.start()] + repl + text[m.end():]
        lines = source.split("\n")
        lines[line - 1] = new_line
        out.append("\n".join(lines))
    return out[:4]


def variants(source: str, function: str, diff: str, attribution: dict | None):
    """`(label, candidate)` for each stated, located residual. Nothing without a source-bound attribution."""
    from solver.source_attribution import sha
    if not diff or not attribution or attribution.get("status") != "verified" \
            or attribution.get("source_sha256") != sha(source):
        return
    seen, count = {source}, 0
    for sig, line, target, cand in sites(diff, attribution):
        if line is None:
            continue
        for candidate in _edits(source, sig, line, target, cand):
            if candidate in seen or count >= CAP:
                continue
            seen.add(candidate)
            count += 1
            yield f"evidence_site:{sig}@{line}", candidate
