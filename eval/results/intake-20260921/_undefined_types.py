"""The last residual class, tested rather than inferred.

Raw cfe output for `clearRaceReplayCourseGrid`:

    cfe: Error: ..., line 9: Syntax Error
         s32 var_v1;
    --------^
    The token read was unexpected.

The caret sits on the identifier, not the type, and `s32 var_v1;` is valid C. The line before it is
`CourseGridEntry *var_v0;`, and if `CourseGridEntry` is not a known type name then cfe treats it as an
expression and dies at the NEXT token. That is why the error lands one statement below its cause.

So: for every still-blocked state, take the first declaration inside the function body whose type name is
not one of C's builtin/primitive spellings, and check whether that type name is declared in the candidate
at all. `strlen` in the earlier small frame is the same shape (`Selector requires struct/union pointer as
left hand side`), and the intake sequence's `opaque-struct` action is supposed to own this class.

Read-only over the post-fix residual; no model.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, gated, rank                      # noqa: E402
from eval.intake_runners import RUNNERS                                  # noqa: E402
from eval.tool_agent_run import build_context                            # noqa: E402

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"

BUILTIN = {"s8", "u8", "s16", "u16", "s32", "u32", "s64", "u64", "f32", "f64", "void", "char",
           "short", "int", "long", "float", "double", "unsigned", "signed", "struct", "union", "enum",
           "const", "volatile", "static", "register", "auto", "M2C_UNK"}
DECL = re.compile(r"(?m)^\s*(?P<type>[A-Za-z_]\w*)\s*\*?\s*(?P<name>[A-Za-z_]\w*)\s*(\[[^\]]*\])?\s*;")
BODY = re.compile(r"(?m)^[A-Za-z_].*\)\s*\{\s*$")

classes = json.loads((BASE / "post-fix-residual-classes.json").read_text(encoding="utf-8"))
blocked = [f for group in classes["classes"].values() for f in group]
unclassified = set(classes["classes"]["unclassified"])
frame = {r["function"]: r for r in
         json.loads((BASE / "class-frame.json").read_text(encoding="utf-8"))["rows"]}

conn = sqlite3.connect(str(KB))
findings = []
try:
    for name in blocked:
        context, why = build_context(REPO, name, conn=conn)
        if context is None:
            continue
        initial = dict(context.initial_verdict or {})
        current, best, stderr = context.candidate, initial, initial.get("stderr") or ""
        for label in SEQUENCE:
            if not gated(label, stderr):
                continue
            ns = {**context.__dict__, "candidate": current, "kb_conn": conn,
                  "initial_verdict": {**initial, "stderr": stderr}}
            try:
                result = RUNNERS[label](ns, {})
            except Exception:                                        # noqa: BLE001
                continue
            if not result.get("changed"):
                continue
            verdict = context.compile_fn(result["source"])
            if rank(verdict) >= rank(best):
                current, best = result["source"], verdict
                stderr = verdict.get("stderr") or ""
        if best.get("compiled"):
            continue
        # only look inside the function body
        lines = current.splitlines()
        start = next((i for i, line in enumerate(lines) if BODY.match(line)), 0)
        unknown = []
        for index in range(start, len(lines)):
            match = DECL.match(lines[index])
            if not match:
                continue
            type_name = match.group("type")
            if type_name in BUILTIN:
                continue
            # declared anywhere in the candidate as a type?
            declared = bool(re.search(rf"\b(typedef\s+[^;]*\b{type_name}\b|struct\s+{type_name}\b|"
                                      rf"union\s+{type_name}\b|enum\s+{type_name}\b)", current))
            unknown.append({"line": index + 1, "type": type_name, "declared": declared,
                            "text": lines[index].strip()[:70]})
            if len(unknown) >= 3:
                break
        first = unknown[0] if unknown else None
        findings.append({"function": name, "unclassified": name in unclassified,
                         "unknown_types": unknown,
                         "first_undefined_type": (first["type"] if first and not first["declared"]
                                                  else None)})
        print(json.dumps({"function": name, "unclassified": name in unclassified,
                          "first_undefined_type": findings[-1]["first_undefined_type"],
                          "declarations": [f"{u['type']}{'' if u['declared'] else ' UNDECLARED'}"
                                           for u in unknown]}), flush=True)
finally:
    conn.close()

print()
counts = Counter(f["first_undefined_type"] is not None for f in findings)
und = [f for f in findings if f["first_undefined_type"]]
print(f"blocked states examined        : {len(findings)}")
print(f"with an UNDECLARED type name   : {len(und)}")
print(f"  of which the unclassified 11 : "
      f"{sum(1 for f in und if f['unclassified'])} of {len(unclassified)}")
names = Counter(f["first_undefined_type"] for f in und)
print(f"\nthe type names cfe chokes on (first per state):")
for type_name, count in names.most_common():
    print(f"  {count:3}  {type_name}")
(BASE / "post-fix-undefined-types.json").write_text(json.dumps(
    {"rows": findings, "undefined": len(und),
     "names": dict(names),
     "note": "first declaration in the body whose type name is not declared anywhere in the candidate"},
    indent=2) + "\n", encoding="utf-8")
print(f"\nwrote {BASE / 'post-fix-undefined-types.json'}")
