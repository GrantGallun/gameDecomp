"""What is still stopping the 31, in the compiler's own words and in the draft's own text.

The previous classification scraped a TRUNCATED stderr (each entry is cut to 120 characters) and, worse,
took whichever action happened to report one -- not the state the sequence ended on. Both make the
answer unreadable, and a taxonomy of the wrong text is worse than none.

So this re-derives each state through the fixed intake sequence, keeps the BEST candidate, and then quotes
the full compiler error for it together with the offending source line. Two things fall out: the residual
class after every repair has been applied, and the actual text cfe refuses -- which is what a fix has to
be written against.

Read-only apart from the attempts the oracle logs; no model, nothing promoted.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import rank                                       # noqa: E402
from eval.intake_runners import RUNNERS                                  # noqa: E402
from eval.tool_agent_run import build_context                            # noqa: E402

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"

# The fixed order, taken from the probe rather than retyped.
from eval.intake_probe import SEQUENCE, gated                            # noqa: E402

ERROR = re.compile(r"cfe: Error: (?P<where>candidate\.c[^:]*):\s*(?P<what>[^\n]*)")
LINE_OF = re.compile(r"line\s+(?P<line>\d+)")

frame = json.loads((BASE / "class-frame.json").read_text(encoding="utf-8"))["rows"]
rows = []
conn = sqlite3.connect(str(KB))
try:
    for entry in frame:
        name = entry["function"]
        context, why = build_context(REPO, name, conn=conn)
        if context is None:
            print(f"{name}: {why}")
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
            rows.append({"function": name, "tier": entry.get("tier"), "compiled": True,
                         "exact": bool(best.get("exact")), "score": best.get("score"),
                         "first_error": "", "source_line": ""})
            continue
        errors = ERROR.findall(stderr or "")
        first = errors[0] if errors else ("", (stderr or "")[:80])
        line_no = None
        match = LINE_OF.search(first[0])
        if match:
            line_no = int(match.group("line"))
        text = ""
        if line_no:
            lines = current.splitlines()
            if 1 <= line_no <= len(lines):
                text = lines[line_no - 1].strip()[:110]
        rows.append({"function": name, "tier": entry.get("tier"), "compiled": False, "exact": False,
                     "score": None, "first_error": first[1].strip()[:70], "line": line_no,
                     "source_line": text, "error_count": len(errors)})
        print(json.dumps(rows[-1]), flush=True)
finally:
    conn.close()

blocked = [r for r in rows if not r["compiled"]]
print()
print("=" * 78)
print(f"THE RESIDUAL AFTER EVERY REPAIR, on the {len(blocked)} still-blocked states")
print("=" * 78)
classes = Counter(r["first_error"] for r in blocked)
for text, count in classes.most_common(12):
    print(f"  {count:3}  {text}")

print()
print("the offending source line, where the compiler named one:")
for row in blocked:
    if row.get("source_line"):
        print(f"  {row['function']:38} line {row['line']:>4}: {row['source_line']}")
    else:
        print(f"  {row['function']:38} (no usable line number)  {row['first_error'][:50]}")

(BASE / "post-fix-residual.json").write_text(json.dumps(
    {"rows": rows, "blocked": len(blocked),
     "classes": dict(classes),
     "note": "first error of the BEST candidate the fixed intake sequence reaches; read-only"},
    indent=2) + "\n", encoding="utf-8")
print(f"\nwrote {BASE / 'post-fix-residual.json'}")
