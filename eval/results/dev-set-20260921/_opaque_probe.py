"""Why `opaque_variant` turns three compiling candidates into uncompilable ones.

For each of the three states the search caught, this prints:
  * the source delta the action produced (added declarations, respelled parameter)
  * the compile verdict before and after
  * the compiler's own words for the failure

Run in WSL:  python -m eval.results.dev-set-20260921._opaque_probe  (PYTHONPATH=/mnt/c/Code/gameDecomp)
"""
from __future__ import annotations

import difflib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from eval.intake_runners import RUNNERS                                   # noqa: E402
from eval.tool_agent_run import build_context                             # noqa: E402

FUNCTIONS = ("updateRacePlayerLeanAngle", "updateRacePlayerAirborneLaunch",
             "updateRacePlayerMode06TerrainFall")
SOURCES = ROOT / "eval/results/dev-set-20260921/sources"
REPO = Path.home() / "decomp/sbk1"

for name in FUNCTIONS:
    print("=" * 78)
    print(name)
    print("=" * 78)
    source = (SOURCES / f"{name}.c").read_text(encoding="utf-8")
    context, why = build_context(REPO, name)
    if context is None:
        print(f"  no context: {why}")
        continue
    before = context.compile_fn(source)
    namespace = {**context.__dict__, "candidate": source, "diff": before.get("diff"),
                 "initial_verdict": before}
    result = RUNNERS["eval.intake_runners.opaque_variant"](namespace, {})
    print(f"  status={result.get('status')} changed={result.get('changed')}")
    if not result.get("changed"):
        print(f"  reason={result.get('reason')}")
        continue
    out = result["source"]
    after = context.compile_fn(out)
    print(f"  compiled {before.get('compiled')} -> {after.get('compiled')}   "
          f"score {before.get('score')} -> {after.get('score')}")
    if after.get("stderr"):
        print("  compiler says:")
        for line in (after["stderr"] or "").splitlines()[:8]:
            print(f"    {line}")
    print("  the action's own receipt:")
    print("   ", json.dumps({k: v for k, v in (result.get("detail") or {}).items()
                             if k not in ("memory_accesses", "plans")})[:600])
    print("  source delta:")
    for line in difflib.unified_diff(source.splitlines(), out.splitlines(), lineterm="", n=1):
        print(f"    {line}")
    print()
