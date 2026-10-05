"""Does the depth-aware body scan reach the actor structs, and do they compile the states?

The six `incomplete definition of type 'X'` states are the ones this pass was BUILT for and it declined on
all of them, with `no body in <file>` while the body sat in the file -- its `\\{([^{}]*)\\}` pattern could not
cross the nested `union { struct { union { ...` these structs are made of.

`_body_span` is depth-aware now. This checks the two things that matter and in this order:
  1. `type_bodies` finds the type
  2. the recovered declaration makes the state's member accesses resolve, judged by the oracle
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, gated, rank                        # noqa: E402
from eval.intake_runners import RUNNERS                                    # noqa: E402
from eval.tool_agent_run import build_context                              # noqa: E402
from solver import compile_obligations, source_type_declarations as std, workspace   # noqa: E402

REPO = Path.home() / "decomp/sbk1"
KB = Path.home() / "decomp/kb-sbk1.sqlite"

STATES = ("updateEndingCreditsCharacterLoopingSparkle", "updateEndingCreditsCharacterVanishPoof",
          "updateFallingActionProjectileLanded", "drawTrainingCourseLessonEndMenu",
          "drawRacePlayerModel", "drawEndingCreditsTumblingSnowboard",
          "updateRaceIntroBillboard", "drawControllerPakRaceRecordSaveScorePanel")


def insert(candidate: str, block: str) -> str:
    import re
    includes = list(re.finditer(r"(?m)^\s*#\s*include\s*[<\"][^>\"]+[>\"]", candidate))
    at = includes[-1].end() if includes else 0
    return candidate[:at] + "\n" + block + candidate[at:]


conn = sqlite3.connect(str(KB))
compiled = 0
try:
    for name in STATES:
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
            except Exception:                                          # noqa: BLE001
                continue
            if not result.get("changed"):
                continue
            verdict = context.compile_fn(result["source"])
            if rank(verdict) >= rank(best):
                current, best = result["source"], verdict
                stderr = verdict.get("stderr") or ""
        if best.get("compiled"):
            print(f"{name}: already compiles")
            continue

        asm = workspace.target_asm(workspace.bootstrap(REPO, name), name)
        analysis, _ = compile_obligations.analyse(asm)
        offsets: dict[str, set[int]] = {}
        for access in analysis.accesses.values():
            if access.address and access.address.kind == "address" \
                    and str(access.address.name).startswith("param"):
                offsets.setdefault(str(access.address.name), set()).add(access.address.offset)
        block, report = std.recover(current, function=name, repo=REPO,
                                    target=str(context.target or ""), assembly=asm,
                                    binary_offsets=offsets)
        status = "RECOVERED" if block else "declined"
        print(f"{name}")
        print(f"   {status}: {[r['type'] for r in report['recovered']]}")
        if report["declined"]:
            print(f"   declined: {report['declined'][0][:110]}")
        if not block:
            print()
            continue
        verdict = context.compile_fn(insert(current, block))
        ok = bool(verdict.get("compiled"))
        compiled += int(ok)
        first = next((ln.strip() for ln in (verdict.get("stderr") or "").splitlines() if ln.strip()), "")
        print(f"   WITH the declaration: compiled={ok} exact={bool(verdict.get('exact'))} "
              f"score={verdict.get('score')}")
        print(f"   cfe: {first[:96] or '(none)'}")
        print()
finally:
    conn.close()
print(f"compiled by the recovered declaration: {compiled} of {len(STATES)}")
