"""Why does `recover` decline `EndingCreditsEffectActor` when `type_bodies` finds it and the draft uses it?"""
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
NAME = "updateEndingCreditsCharacterLoopingSparkle"

conn = sqlite3.connect(str(Path.home() / "decomp/kb-sbk1.sqlite"))
try:
    context, why = build_context(REPO, NAME, conn=conn)
    if context is None:
        raise SystemExit(why)
    initial = dict(context.initial_verdict or {})
    current, best, stderr = context.candidate, initial, initial.get("stderr") or ""
    for label in SEQUENCE:
        if not gated(label, stderr):
            continue
        ns = {**context.__dict__, "candidate": current, "kb_conn": conn,
              "initial_verdict": {**initial, "stderr": stderr}}
        try:
            result = RUNNERS[label](ns, {})
        except Exception:                                                  # noqa: BLE001
            continue
        if not result.get("changed"):
            continue
        verdict = context.compile_fn(result["source"])
        if rank(verdict) >= best[0] if False else rank(verdict) >= rank(best):
            current, best = result["source"], verdict
            stderr = verdict.get("stderr") or ""

    print(f"signature: "
          f"{next((ln for ln in current.splitlines() if NAME + '(' in ln), '').strip()[:100]}")
    print(f"types referenced: {std.referenced_types(current, NAME)}")
    print(f"parameters: {std.parameter_names(current)}")
    print(f"declared in the candidate already: {sorted(set(std.DECLARED_IN.findall(current)))}")

    path = std.target_source_file(REPO, str(context.target or ""))
    print(f"source file: {path}")
    bodies = std.type_bodies(path.read_text(encoding="utf-8", errors="replace")) if path else {}
    record = bodies.get("EndingCreditsEffectActor")
    print(f"body found: {record is not None}")

    # The pieces of the gate.
    index = std.parameter_index(current, "EndingCreditsEffectActor")
    print(f"parameter_index -> {index}")
    params = std.parameter_names(current)
    print(f"params -> {params}")
    if record is not None:
        bases = {params[index]} if index is not None and index < len(params) else set()
        via = std.members_through(current, bases)
        print(f"members_through({bases}) -> {sorted(via)[:10]}")
        print(f"  in the declaration : {sorted(m for m in via if m in record['members'])}")
        print(f"  NOT in the declaration : {sorted(m for m in via if m not in record['members'])}")

    asm = workspace.target_asm(workspace.bootstrap(REPO, NAME), NAME)
    analysis, _ = compile_obligations.analyse(asm)
    offsets: dict[str, set[int]] = {}
    for access in analysis.accesses.values():
        if access.address and access.address.kind == "address" \
                and str(access.address.name).startswith("param"):
            offsets.setdefault(str(access.address.name), set()).add(access.address.offset)
    print(f"binary offsets per parameter: { {k: sorted(v) for k, v in offsets.items()} }")
    block, report = std.recover(current, function=NAME, repo=REPO, target=str(context.target or ""),
                                assembly=asm, binary_offsets=offsets)
    print(f"\nrecover -> block={bool(block)}")
    print(f"  recovered : {[r['type'] for r in report['recovered']]}")
    print(f"  declined  : {report['declined']}")
    print(f"  corroborated: {list(report['corroborated'])}")
    print(f"  carried     : {list(report['carried'])}")
finally:
    conn.close()
