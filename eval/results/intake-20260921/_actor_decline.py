"""Why does `source_type_declarations` decline on the actor-struct states it was built for?

Six of the ten `unclassified` single-class states fail with `incomplete definition of type 'X'` where X is an
actor defined in the function's own `src/*.c` -- exactly this pass's motivating residual, and it recovered
33 declarations on the frame without converting any of these. The receipt names a reason per state; this
prints those reasons rather than re-deriving them.

Also: `alSynSetPan` has `use of undeclared identifier 'bitwise'`, which is m2c's pseudo-cast -- and
`solver/m2c_context.lower_bitcasts` exists for it and is NOT in the sequence. Checked here for reference.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, gated, rank                        # noqa: E402
from eval.intake_runners import RUNNERS                                    # noqa: E402
from eval.tool_agent_run import build_context                              # noqa: E402

REPO = Path.home() / "decomp/sbk1"
KB = Path.home() / "decomp/kb-sbk1.sqlite"
BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")

payload = json.loads((BASE / "wide-intake-orfix.json").read_text(encoding="utf-8"))
STATES = ("updateEndingCreditsCharacterLoopingSparkle", "updateEndingCreditsCharacterVanishPoof",
          "drawRacePlayerModel", "updateFallingActionProjectileLanded",
          "drawTrainingCourseLessonEndMenu", "drawEndingCreditsTumblingSnowboard",
          "updateRaceIntroBillboard", "drawControllerPakRaceRecordWriteScorePanel",
          "drawControllerPakRaceRecordSaveScorePanel", "alSynSetPan")

by_name = {r["function"]: r for r in payload["rows"]}
conn = sqlite3.connect(str(KB))
try:
    for name in STATES:
        row = by_name.get(name)
        if row is None:
            print(f"{name}: not in the frame")
            continue
        print("=" * 78)
        print(f"{name}")
        print("=" * 78)
        entry = row["actions"].get("eval.intake_runners.source_type_declarations") or {}
        detail = entry.get("detail") or {}
        print(f"  source_type_declarations: status={entry.get('status')}")
        for reason in (detail.get("declined") or [])[:3]:
            print(f"     declined: {reason}")
        for item in (detail.get("recovered") or [])[:3]:
            print(f"     recovered: {item['type']} from {item['from']} param={item.get('parameter')}")

        context, why = build_context(REPO, name, conn=conn)
        if context is None:
            print(f"  build_context: {why}")
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
            except Exception:                                          # noqa: BLE001
                continue
            if not result.get("changed"):
                continue
            verdict = context.compile_fn(result["source"])
            if rank(verdict) >= rank(best):
                current, best = result["source"], verdict
                stderr = verdict.get("stderr") or ""
        signature = next((ln for ln in current.splitlines() if name + "(" in ln), "")
        print(f"  signature : {signature.strip()[:100]}")
        print(f"  cfe       : "
              f"{next((ln.strip() for ln in stderr.splitlines() if ln.strip()), '(none)')[:90]}")
        from solver import source_type_declarations as std
        types = std.referenced_types(current, name)
        print(f"  referenced_types(source) -> {types}")
        print(f"  target    : {context.target}")
        path = std.target_source_file(REPO, str(context.target or ""))
        print(f"  source file: {path.name if path else '(none -- target does not map to src/)'}")
        if path:
            bodies = std.type_bodies(path.read_text(encoding="utf-8", errors="replace"))
            for type_name in types:
                print(f"     {type_name}: {'BODY PRESENT' if type_name in bodies else 'no body in that file'}")
        print()
finally:
    conn.close()
