"""The map brief: faulty C lines with their target/candidate instructions, stated fixes, and cross-function examples."""
from __future__ import annotations

import collections
import difflib
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import mechanism_roadmap  # noqa: E402
from solver import alignment, evidence_site  # noqa: E402
from solver.source_attribution import instructions_of, sha  # noqa: E402

STATED = {
    "field:offset": "the target accesses a different offset here: the field or index this line uses is wrong",
    "field:immediate": "the target uses a different constant here",
    "field:symbol": "the target references a different global here",
    "opcode": "the target accesses memory with a different width or signedness here: the C type on this line differs",
    "extra": "the target has no such instruction: this line computes something (a mask, shift, add or cast) the "
             "original does not",
}


def hint(cls: str, target: str | None, cand: str | None) -> str:
    """A stated meaning only where the diff actually states it; nothing otherwise."""
    op_t, op_c = (target or "").split()[:1], (cand or "").split()[:1]
    if cand and cand.replace(" ", "").startswith("addiusp,sp,"):
        return ("the stack frame size differs: the original keeps a different number of locals in memory "
                "(or passes more/fewer arguments on the stack)")
    if cls in ("field:offset", "field:immediate", "field:symbol"):
        return STATED[cls]
    if cls.startswith("opcode:") and op_t and op_c and op_t[0] in evidence_site.LOADSTORE \
            and op_c[0] in evidence_site.LOADSTORE:
        return STATED["opcode"]
    if cls.startswith("extra:") and op_c and op_c[0] in evidence_site.OPERATOR and "%" not in (cand or ""):
        return STATED["extra"]
    return ""


def per_line(verdict: dict, source: str) -> dict[int, list[tuple[str, str | None, str | None]]]:
    attribution = verdict.get("source_attribution") or {}
    if attribution.get("status") != "verified" or attribution.get("source_sha256") != sha(source):
        return {}
    diff = verdict.get("diff") or ""
    where = {r["normalized_line"]: r.get("candidate_line") for r in instructions_of(attribution)}
    stream = evidence_site._candidate_stream_lines(diff)
    lines = collections.defaultdict(list)
    for (cls, _stated, _loc), step in zip(mechanism_roadmap.classes(diff, attribution),
                                         [s for s in alignment.align_diff(diff).steps if not s.ambiguous and not (
                                             s.target is not None and s.candidate is not None
                                             and s.target.text == s.candidate.text)]):
        c = step.candidate
        if c is not None and c.index < len(stream) and where.get(stream[c.index]):
            lines[where[stream[c.index]]].append((cls, step.target.text if step.target else None, c.text))
    return lines


def examples(index: list[dict], classes: set[str], exclude: str, k: int = 2) -> list[dict]:
    out, seen = [], set()
    for e in index:
        key = (e["before"], e["after"])
        if e["function"] != exclude and e["class"] in classes and key not in seen and e["class"] != "field:branch":
            seen.add(key)
            out.append(e)
    # prefer examples from different residual classes present here
    out.sort(key=lambda e: sorted(classes).index(e["class"]) if e["class"] in classes else 99)
    picked, used = [], set()
    for e in out:
        if e["class"] not in used:
            picked.append(e)
            used.add(e["class"])
        if len(picked) == k:
            break
    return picked


def build(function: str, verdict: dict, source: str, index: list[dict]) -> str:
    lines = per_line(verdict, source)
    if not lines:
        return ""
    src = source.split("\n")
    parts = ["COMPILER-ATTRIBUTED FAULTS. The compiler's own line records map each differing instruction to the "
             "C line that produced it. Edit these lines; lines not listed compile correctly."]
    classes = set()
    shown = 0
    for line in sorted(lines):
        # Branch-target differences follow from instructions shifting elsewhere; they are not this line's fault.
        rows = [r for r in lines[line] if r[0] != "field:branch"]
        if not rows or shown >= 10:
            continue
        shown += 1
        classes.update(c for c, _t, _c in rows)
        parts.append(f"\nline {line}: {src[line - 1].strip() if 0 < line <= len(src) else ''}")
        for cls, target, cand in rows[:4]:
            parts.append(f"  target `{target or '(none)'}`  yours `{cand or '(none)'}`"
                         + (f"  -- {h}" if (h := hint(cls, target, cand)) else ""))
    ex = examples(index, classes, function)
    if ex:
        parts.append("\nEDITS THAT FIXED THE SAME KIND OF FAULT IN OTHER FUNCTIONS (pattern only, not their code):")
        for e in ex:
            parts.append(f"  [{e['class']}] `{e['before']}`  ->  `{e['after']}`")
    return "\n".join(parts)


def example_index(rows_dirs: list[Path]) -> list[dict]:
    """Improving recorded edits that changed exactly one line which carried a residual that then disappeared."""
    out = []
    for d in rows_dirs:
        for path in sorted(d.glob("*.json")):
            row = json.loads(path.read_text())
            if not row.get("world"):
                continue
            world = json.loads(Path(row["world"]).read_text())["world"]
            nodes = {n["id"]: n for n in world["nodes"]}
            for n in world["nodes"]:
                if n["parent"] is None or not n["verdict"]["compiled"]:
                    continue
                p = nodes[n["parent"]]
                if not p["verdict"]["compiled"] or n["verdict"]["score"] <= p["verdict"]["score"]:
                    continue
                a, b = p["source"].split("\n"), n["source"].split("\n")
                ops = [o for o in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes() if o[0] != "equal"]
                if len(ops) != 1 or ops[0][0] != "replace" or ops[0][2] - ops[0][1] != 1 or ops[0][4] - ops[0][3] != 1:
                    continue
                line = ops[0][1] + 1
                before_faults = per_line(p["verdict"], p["source"]).get(line, [])
                if not before_faults:
                    continue
                out.append({"function": row["function"], "class": before_faults[0][0],
                            "before": a[ops[0][1]].strip(), "after": b[ops[0][3]].strip()})
    return out
