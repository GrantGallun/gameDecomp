"""Is the remaining 31 a missing capability, or a flag?

The residual after every repair is dominated by constructs that are not C:

    temp_v0->unk-4 = (s32) filter->unk14;          # `unk-4` is not an identifier
    var_v0_5->unk-604 = 9;
    temp_v0->data.f = (bitwise f32) pan;           # a cast to a non-type
    temp_t9->unk0 = (unaligned s32) temp_a1->unk0;
    /* 0x00 */ ? unk0;                             # a `?` inside a struct body

m2c can emit all of these in a form cfe accepts: `m2c_input.draft(..., valid_syntax=True)` passes
`--valid-syntax`, and the repository already uses that flag in `compile_recovery` and
`callee_prototype_repair`. The intake route does not: `workspace.m2c_draft` calls
`m2c_input.draft(repo, target.s)` with defaults. `solver/m2c_context.py` separately lowers
`(bitwise f32)` into union form.

So the question this answers is narrow and mechanical: on the same 40 states, what does the campaign's
intake sequence convert when the draft is produced with `valid_syntax=True`? Same frame, same actions,
same oracle as `post-fix-intake.json`; the ONLY difference is the flag. Nothing is promoted and the flag
is not wired anywhere by this script.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, gated, rank                      # noqa: E402
from eval.intake_runners import RUNNERS                                  # noqa: E402
from eval.tool_agent import Context                                      # noqa: E402
from solver import m2c_input, workspace                                  # noqa: E402

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"

frame = json.loads((BASE / "class-frame.json").read_text(encoding="utf-8"))["rows"]
conn = sqlite3.connect(str(KB))
rows = []
try:
    for entry in frame:
        name = entry["function"]
        ws = workspace.bootstrap(REPO, name)
        if not (ws / "target.s").is_file():
            print(f"{name}: no target.s")
            continue
        # THE ONLY DIFFERENCE FROM THE INTAKE ARM: this flag.
        result, meta = m2c_input.draft(REPO, ws / "target.s", valid_syntax=True)
        if result.returncode != 0 or not result.stdout.strip():
            rows.append({"function": name, "tier": entry.get("tier"), "drafted": False,
                         "compiled": False, "exact": False, "score": None})
            print(json.dumps(rows[-1]), flush=True)
            continue
        candidate = '#include "common.h"\n\n' + result.stdout

        def compile_fn(source: str, _ws=ws, _fn=name) -> dict:
            from eval.tool_agent_run import _attempt_to_verdict
            attempt = workspace.score(_ws, REPO, _fn, source, conn=conn, func=_fn)
            return _attempt_to_verdict(attempt)

        initial = compile_fn(candidate)
        context = Context(function=name, candidate=candidate,
                          target_asm_path=str(ws / "target.s"), source_path=str(ws / "base.c"),
                          compile_fn=compile_fn, repo=str(REPO), workspace=str(ws),
                          initial_verdict=initial)
        current, best, stderr = candidate, initial, initial.get("stderr") or ""
        fired = []
        for label in SEQUENCE:
            if not gated(label, stderr):
                continue
            ns = {**context.__dict__, "candidate": current, "kb_conn": conn,
                  "initial_verdict": {**initial, "stderr": stderr}}
            try:
                action = RUNNERS[label](ns, {})
            except Exception:                                        # noqa: BLE001
                continue
            if not action.get("changed"):
                continue
            verdict = compile_fn(action["source"])
            if rank(verdict) >= rank(best):
                current, best = action["source"], verdict
                stderr = verdict.get("stderr") or ""
                fired.append(label.split(".")[-1])
        rows.append({"function": name, "tier": entry.get("tier"), "drafted": True,
                     "baseline_compiled": bool(initial.get("compiled")),
                     "compiled": bool(best.get("compiled")), "exact": bool(best.get("exact")),
                     "score": best.get("score"), "fired": fired,
                     "first_error": next((ln for ln in (best.get("stderr") or "").splitlines()
                                          if ln.strip()), "")[:90]})
        print(json.dumps({k: rows[-1][k] for k in ("function", "tier", "compiled", "exact", "score")}),
              flush=True)
finally:
    conn.close()

converted = sum(1 for r in rows if r["compiled"])
exact = sum(1 for r in rows if r["exact"])
print()
print(f"valid_syntax arm : converted {converted} of {len(rows)} "
      f"({100 * converted / len(rows):.1f}%), exact {exact}, drafted {sum(1 for r in rows if r['drafted'])}")
(BASE / "post-fix-valid-syntax.json").write_text(json.dumps(
    {"rows": rows, "converted": converted, "exact": exact,
     "note": ("m2c_input.draft(..., valid_syntax=True) then the campaign's intake sequence. Same frame, "
              "same actions, same oracle as post-fix-intake.json; the flag is the only difference. "
              "Measured, not wired.")}, indent=2) + "\n", encoding="utf-8")
print(f"wrote {BASE / 'post-fix-valid-syntax.json'}")
