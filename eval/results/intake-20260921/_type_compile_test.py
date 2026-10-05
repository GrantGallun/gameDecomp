"""Does the recovered declaration compile the real states? The decisive test, on the two known cases."""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.tool_agent_run import build_context                              # noqa: E402
from solver import compile_obligations, source_type_declarations as std, workspace   # noqa: E402

REPO = Path.home() / "decomp/sbk1"
NAMES = ("renderPickupShardParticle", "renderRaceCourseTripleParticle",
         "renderRacePickupRespawn", "drawTrainingCourseLessonEndMenu")


def insert(candidate: str, block: str) -> str:
    """After the last #include -- the same placement the runner uses.

    The first version of this test prepended the block, so the struct landed ABOVE `#include "common.h"`
    and cfe reported `Syntax Error` at the struct's first line: the type names in the recovered declaration
    are not declared yet. That is a defect in the test rather than in the recovery, and it is worth naming
    because a block inserted in the wrong place looks exactly like a block that does not work.
    """
    includes = list(re.finditer(r"(?m)^\s*#\s*include\s*[<\"][^>\"]+[>\"]", candidate))
    at = includes[-1].end() if includes else 0
    return candidate[:at] + "\n" + block + candidate[at:]

conn = sqlite3.connect(str(Path.home() / "decomp/kb-sbk1.sqlite"))
try:
    for name in NAMES:
        context, why = build_context(REPO, name, conn=conn)
        if context is None:
            print(f"{name}: {why}")
            continue
        asm = workspace.target_asm(workspace.bootstrap(REPO, name), name)
        analysis, _ = compile_obligations.analyse(asm)
        offsets: dict[str, set[int]] = {}
        for access in analysis.accesses.values():
            if access.address and access.address.kind == "address" \
                    and str(access.address.name).startswith("param"):
                offsets.setdefault(str(access.address.name), set()).add(access.address.offset)

        candidate = context.candidate or ""
        base = context.compile_fn(candidate)
        block, report = std.recover(candidate, function=name, repo=REPO,
                                    target=str(context.target or ""), assembly=asm,
                                    binary_offsets=offsets)
        print(f"{name}")
        print(f"   baseline   : compiled={bool(base.get('compiled'))} score={base.get('score')}")
        print(f"   recovered  : {[r['type'] for r in report['recovered']]} "
              f"corroborated={list(report['corroborated'])}")
        if report["declined"]:
            print(f"   declined   : {report['declined'][:2]}")
        if not block:
            print()
            continue
        verdict = context.compile_fn(insert(candidate, block))
        print(f"   WITH block : compiled={bool(verdict.get('compiled'))} "
              f"exact={bool(verdict.get('exact'))} score={verdict.get('score')}")
        first = next((ln.strip() for ln in (verdict.get("stderr") or "").splitlines() if ln.strip()), "")
        print(f"   cfe        : {first[:96] or '(none)'}")
        print()
finally:
    conn.close()
