"""Re-rank the whole-distance classes on the newest payload, and test `undeclared_identifiers`.

WHY THIS MECHANISM. `solver/undeclared_identifiers.py` exists and is documented as the owner of the
`'X' undefined` class -- the largest whole-distance class after the type recovery (30 states on the last
read). It declares only names IDO itself reports, with the least committal declaration that compiles:

    spNN used by address or index   u8 spNN[size]      global used only by address   extern u8 NAME;
    spNN used as a value            s32 spNN;          global indexed                extern s32 NAME[];
    bare `unkNN`                    DECLINED (a flattened struct member)              global by value  extern s32 NAME;

It is NOT in the intake sequence and NOT in the action registry. Before wiring anything, this measures what
it would do on the frame: how often it fires, and what the resulting candidate compiles to.

Read-only apart from the attempts the oracle logs.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, gated, rank                        # noqa: E402
from eval.intake_runners import RUNNERS                                    # noqa: E402
from eval.tool_agent_run import build_context                              # noqa: E402
from solver import undeclared_identifiers                                  # noqa: E402

REPO = Path.home() / "decomp/sbk1"
KB = Path.home() / "decomp/kb-sbk1.sqlite"
BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")

traced = json.loads((BASE / "wide-intake-types.json").read_text(encoding="utf-8"))
blocked = [row for row in traced["rows"] if not row["sequence"]["compiled"]]

# The final class set per state, from the newest traced run.
whole: Counter = Counter()
for row in blocked:
    classes = (row.get("diagnostic_trace") or [{}])[-1].get("classes") or []
    if len(classes) == 1:
        whole[classes[0]] += 1
print(f"blocked: {len(blocked)} of {len(traced['rows'])}")
print(f"whole-distance classes, newest payload: {dict(whole.most_common())}\n")

# The states whose whole distance is the undeclared-identifier class: the mechanism's motivating residual.
targets = [row["function"] for row in blocked
           if (row.get("diagnostic_trace") or [{}])[-1].get("classes") == ["undeclared-identifier"]]
print(f"states one undeclared-identifier fix from compiling: {len(targets)}")
for name in targets:
    print(f"   {name}")

conn = sqlite3.connect(str(KB))
fired = converted = exact = 0
names_seen: Counter = Counter()
declines: Counter = Counter()
try:
    for name in targets:
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

        # The mechanism's own contract: it reads compiler-reported names, and `propose` returns
        # `([(label, source)], report)`.
        reported = undeclared_identifiers.undefined_names(stderr)
        if not reported:
            declines["the final cfe diagnostics name no undefined identifier"] += 1
            continue
        names_seen.update(reported)
        try:
            variants, report = undeclared_identifiers.propose(current, name, stderr)
        except Exception as exc:                                       # noqa: BLE001
            declines[f"raised {type(exc).__name__}: {exc}"] += 1
            continue
        if not variants:
            for item in (report.get("declines") or report.get("declined") or []):
                declines[str(item)[:60]] += 1
            if not (report.get("declines") or report.get("declined")):
                for key in ("reason", "status", "unresolved"):
                    if report.get(key):
                        declines[f"{key}: {str(report[key])[:52]}"] += 1
                        break
                else:
                    declines["declined without naming a reason"] += 1
            continue
        source = variants[0][1]
        verdict = context.compile_fn(source)
        fired += 1
        converted += int(bool(verdict.get("compiled")))
        exact += int(bool(verdict.get("exact")))
        print(f"   {name:44} FIRED -> compiled={bool(verdict.get('compiled'))} "
              f"score={verdict.get('score')} names={reported[:4]}")
finally:
    conn.close()

print(f"\nfired on {fired} of {len(targets)}")
print(f"  compiled by the mechanism alone: {converted}, exact: {exact}")
print(f"\nthe names it was asked to declare:")
for name, count in names_seen.most_common(14):
    print(f"   {count:4}  {name}")
print(f"\nwhy it declined:")
for reason, count in declines.most_common(10):
    print(f"   {count:4}  {reason[:84]}")
