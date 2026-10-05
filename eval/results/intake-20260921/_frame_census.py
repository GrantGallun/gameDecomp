"""What are the 40 drafts actually blocked on, and is the `?` placeholder the thing in front of them?

Two questions the two arms raised and neither answers:

  A. WHAT CLASS IS THE HEAD BLOCKER? The frame is drawn from the largest measured class, "cfe: Syntax
     Error". That is a SYMPTOM: the demand queue already established `Syntax Error` is half of
     everything and names no cause. If most of these drafts carry m2c's `?` placeholder, then the
     blocker is `solver/m2c_placeholders.py`'s class and one action owns it -- and that action was
     already in the ORIGINAL space while being absent from the four intake actions.

  B. DOES THE INTAKE ROUTE WORK ONCE THE PLACEHOLDER IS OUT OF THE WAY? The intake sequence never runs
     `resolve-placeholders`, so on any draft where a `?` masks the whole file it is judging
     `header-context` on a candidate the compiler cannot get past line 5 of. Run the placeholder
     rewrite FIRST, then the intake sequence, and compare. If the intake route converts after the
     placeholder is resolved, the two are a COMPOSITION and the "intake lever" was measured on the
     wrong states.

Both are measured over the frame; nothing is promoted.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_control import error_classes                          # noqa: E402
from eval.intake_probe import SEQUENCE, rank                           # noqa: E402
from eval.intake_runners import RUNNERS                                # noqa: E402
from eval.tool_agent_run import build_context                          # noqa: E402
from eval.tool_runners import resolve_placeholders                     # noqa: E402
from solver import m2c_placeholders                                    # noqa: E402

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"

frame = json.loads((BASE / "class-frame.json").read_text(encoding="utf-8"))["rows"]
print(f"frame: {len(frame)} functions, all measured as not compiling\n")

conn = sqlite3.connect(str(KB))
census = Counter()
head = Counter()
composed: list[dict] = []
try:
    for entry in frame:
        name = entry["function"]
        context, why = build_context(REPO, name, conn=conn)
        if context is None:
            print(f"  {name}: build_context failed: {why}")
            continue
        draft = context.candidate or ""
        found = m2c_placeholders.placeholders(draft)
        classes = Counter()
        for line in ((context.initial_verdict or {}).get("stderr") or "").splitlines():
            if line.startswith("cfe:") and ":" in line:
                classes[line.split(":", 2)[-1].strip()[:52]] += 1
        head_class = classes.most_common(1)[0][0] if classes else "<none>"
        census["placeholders"] += int(bool(found))
        census["no_placeholder"] += int(not found)
        head[head_class] += 1

        # B: placeholder first, then the intake sequence on the result.
        namespace = {**context.__dict__, "kb_conn": conn}
        resolved = resolve_placeholders(namespace, {})
        current = resolved["source"] if resolved.get("changed") else draft
        best, stderr = context.compile_fn(current), ""
        stderr = (best.get("stderr") or "")
        for label in SEQUENCE:
            stage_ns = {**context.__dict__, "candidate": current, "kb_conn": conn,
                        "initial_verdict": {**(context.initial_verdict or {}), "stderr": stderr}}
            try:
                result = RUNNERS[label](stage_ns, {})
            except Exception as exc:                                    # noqa: BLE001
                continue
            if not result.get("changed"):
                continue
            verdict = context.compile_fn(result["source"])
            if rank(verdict) >= rank(best):
                current, best = result["source"], verdict
                stderr = verdict.get("stderr") or ""
        composed.append({"function": name, "tier": entry.get("tier"),
                         "placeholders": len(found),
                         "placeholder_resolved": bool(resolved.get("changed")),
                         "compiled": bool(best.get("compiled")), "exact": bool(best.get("exact")),
                         "score": best.get("score"),
                         "head_class": head_class})
    print("A. the head blocker on this frame:")
    for text, count in head.most_common(12):
        print(f"   {count:3}  {text}")
    print(f"\n   drafts carrying an m2c `?` placeholder: {census['placeholders']} of {len(frame)}")
    print(f"   drafts with no placeholder:             {census['no_placeholder']}")

    print("\nB. placeholder rewrite FIRST, then the intake sequence:")
    converted = [r for r in composed if r["compiled"]]
    print(f"   converted: {len(converted)} of {len(composed)}, "
          f"exact: {sum(1 for r in composed if r['exact'])}")
    for row in converted:
        print(f"      {row['function']:38} {row['tier']:7} placeholders={row['placeholders']:2} "
              f"score={row['score']} exact={row['exact']}")
    print("\n   per-state:")
    for row in composed:
        print(f"      {row['function']:38} {row['tier']:7} ph={row['placeholders']:2} "
              f"resolved={int(row['placeholder_resolved'])} compiled={int(row['compiled'])} "
              f"exact={int(row['exact'])} head={row['head_class'][:44]}")
finally:
    conn.close()

(BASE / "class-composition.json").write_text(json.dumps(
    {"census": dict(census), "head_blockers": dict(head), "rows": composed,
     "converted": sum(1 for r in composed if r["compiled"]),
     "exact": sum(1 for r in composed if r["exact"]),
     "note": "placeholder rewrite first, then the campaign's intake sequence; no model, nothing promoted"},
    indent=2) + "\n", encoding="utf-8")
print(f"\nwrote {BASE / 'class-composition.json'}")
