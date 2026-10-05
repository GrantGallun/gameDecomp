"""What are the conversions WORTH? Run the repair machinery on the 9 states the placeholder rewrite opens.

A state that compiles at score 95 is not a match, and this project counts matches. The four actions that
exist for a COMPILING candidate -- `invert-mutations`, `diffrepair`, `regalloc-search` and `redraft` --
never got a chance to run on any of these 40 states, because the frame was selected for drafts that do not
compile and every one of them dies at the front door. If the union rewrite opens the door, those actions
become applicable for the first time, and the question "does the intake lever generalise" turns into
"does the intake lever feed the rest of the pipeline".

Measured for all 40 frame members, from the union-rewritten candidate:

    union alone        the compiling state the rewrite produces
    + invert-mutations deterministic inverses of the mutation catalogue
    + regalloc-search  gradient beam search over register-allocation mutations, budget 64

`diffrepair` is omitted deliberately and reported as such: it declined on 40 of 40 in the control because
`build_context` hands the context `diff=None` for a candidate that does not compile, and it is left out
here rather than silently scored as a failed attempt. The certificate judges every candidate.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_control import build_frame_context                     # noqa: E402
from eval.tool_runners import invert_mutations, regalloc_search         # noqa: E402

sys.path.insert(0, "/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
from _placeholder_rules import union                                   # noqa: E402

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"

frame = json.loads((BASE / "class-frame.json").read_text(encoding="utf-8"))["rows"]
conn = sqlite3.connect(str(KB))
rows = []
try:
    for entry in frame:
        context, initial, drift = build_frame_context(REPO, entry, conn)
        if context is None:
            print(f"  {entry['function']}: {drift}")
            continue
        source, tokens = union(context.candidate or "")
        base = context.compile_fn(source) if source != context.candidate else dict(initial)
        row = {"function": entry["function"], "tier": entry.get("tier"), "tokens": tokens,
               "union_compiled": bool(base.get("compiled")), "union_exact": bool(base.get("exact")),
               "union_score": base.get("score"), "invert": None, "regalloc": None}

        if base.get("compiled") and not base.get("exact"):
            namespace = {**context.__dict__, "candidate": source, "kb_conn": conn}
            result = invert_mutations(namespace, {"combinations": True})
            produced = result.get("candidates") or []
            best = (bool(base.get("exact")), float(base.get("score") or 0.0))
            for candidate in produced:
                verdict = context.compile_fn(candidate["source"])
                best = max(best, (bool(verdict.get("exact")), float(verdict.get("score") or 0.0)))
                if verdict.get("exact"):
                    break
            row["invert"] = {"produced": len(produced), "best_score": best[1], "exact": best[0]}

            result = regalloc_search({**context.__dict__, "candidate": source, "kb_conn": conn},
                                     {"budget": 64, "beam": 8})
            row["regalloc"] = {"exact": bool(result.get("exact")), "compiles": result.get("compiles"),
                               "best_label": result.get("best_label"),
                               "searched": result.get("status") != "not-applicable"}
        rows.append(row)
        print(json.dumps(row), flush=True)
finally:
    conn.close()

n = len(rows)
print()
print(f"frame members measured            : {n}")
print(f"union rewrite compiles            : {sum(1 for r in rows if r['union_compiled'])} "
      f"({100 * sum(1 for r in rows if r['union_compiled']) / n:.1f}%)")
print(f"union rewrite exact               : {sum(1 for r in rows if r['union_exact'])}")
searched = [r for r in rows if r["regalloc"] and r["regalloc"]["searched"]]
print(f"states that reached the repair stage: {len(searched)}")
print(f"  invert-mutations best score       : "
      f"{[r['invert']['best_score'] for r in searched]}")
print(f"  regalloc-search exact             : {sum(1 for r in searched if r['regalloc']['exact'])}")
print(f"certified matches, end to end       : "
      f"{sum(1 for r in rows if r['union_exact'] or (r['regalloc'] or {}).get('exact'))}")

(BASE / "class-repair-stage.json").write_text(json.dumps(
    {"rows": rows,
     "union_compiled": sum(1 for r in rows if r["union_compiled"]),
     "union_exact": sum(1 for r in rows if r["union_exact"]),
     "reached_repair": len(searched),
     "regalloc_exact": sum(1 for r in searched if r["regalloc"]["exact"]),
     "note": ("the union placeholder rewrite opens the front door; the repair actions run on what "
              "compiles. diffrepair is omitted: it had no diff on any of these states. Nothing "
              "promoted; the certificate judged everything.")}, indent=2) + "\n", encoding="utf-8")
print(f"\nwrote {BASE / 'class-repair-stage.json'}")
