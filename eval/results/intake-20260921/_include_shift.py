"""For the 8 states where a header declares the missing type: does adding it remove THAT error?

"Still fails" would also be the answer if the include did nothing at all, so the claim needs the error to
MOVE. This prints the compiler's first error before and after for each, with the line number, so
"the blocker advanced to the next layer" is distinguishable from "adding the include achieved nothing".
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, gated, rank                      # noqa: E402
from eval.intake_runners import RUNNERS                                  # noqa: E402
from eval.tool_agent_run import build_context                            # noqa: E402

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"
INCLUDE = REPO / "include"

CASES = {
    "updateRacePlayerGroundAlignment": "game/math/geometry.h",
    "applyMainMenuSceneModelAnimationFrame": "game/menu/main_menu/main_menu_scene_model.h",
    "updateCourseSelectCourseList": "game/race/player/race_player_input.h",
    "updateMultiplayerCourseSelectMenu": "game/race/camera/race_camera.h",
    "saveRaceRecordReplayData": "game/save_data.h",
    "updateControllerInputState": "game/engine/controller_input.h",
    "updateRaceResultsFlow": "game/race/player/race_player_input.h",
    "renderRacePickupIdle": "game/math/geometry.h",
}
LINE = re.compile(r"line\s+(?P<line>\d+):\s*(?P<what>[^\n]*)")


def first_error(stderr: str) -> tuple[str, str]:
    match = LINE.search(stderr or "")
    if not match:
        return "", (stderr or "").strip().splitlines()[0][:60] if stderr.strip() else ""
    return match.group("line"), match.group("what").strip()[:60]


conn = sqlite3.connect(str(KB))
try:
    print(f"  {'function':40} {'type error before':>22}  ->  {'after adding the header':>26}")
    for name, header in CASES.items():
        context, why = build_context(REPO, name, conn=conn)
        if context is None:
            print(f"  {name}: {why}")
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
        before = first_error(stderr)
        path = INCLUDE / header
        if not path.is_file():
            print(f"  {name:40} {header} MISSING")
            continue
        verdict = context.compile_fn(f'#include "{header}"\n' + current)
        after = first_error(verdict.get("stderr") or "")
        status = "COMPILED" if verdict.get("compiled") else "  "
        print(f"  {name:40} line {before[0]:>4} {before[1][:16]:16} -> {status} "
              f"line {after[0]:>4} {after[1][:24]}")
finally:
    conn.close()
