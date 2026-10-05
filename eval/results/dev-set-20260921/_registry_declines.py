"""Why the whole registry declines on a finished candidate: the named reason for each action.

`search-registry-policy.json` reports zero compiles across all 17 states and all ten registered transforms,
including `regalloc-search` -- which is the operator for the residual the codegen reading found (register
allocation, instruction delta 0 or 1). "No change" is not an answer; this prints what each action said.

Run in WSL:
  PYTHONPATH=/mnt/c/Code/gameDecomp python -m eval.results.dev-set-20260921._registry_declines
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from eval.tool_agent_run import build_context                              # noqa: E402
from eval.tool_registry import ACTIONS                                     # noqa: E402

FUNCTIONS = ("__MusIntProcessWobble", "osEPiRawWriteIo")
SOURCES = ROOT / "eval/results/dev-set-20260921/sources"
REPO = Path.home() / "decomp/sbk1"
OUT = ROOT / "eval/results/dev-set-20260921/registry-declines.json"

report = {}
for name in FUNCTIONS:
    source = (SOURCES / f"{name}.c").read_text(encoding="utf-8")
    context, why = build_context(REPO, name)
    if context is None:
        print(f"{name}: no context ({why})")
        continue
    verdict = context.compile_fn(source)
    print("=" * 78)
    print(f"{name}  score={verdict.get('score')}  target_dump={'yes' if context.target_dump else 'no'} "
          f"workspace={context.workspace}")
    rows = {}
    for action, entry in ACTIONS.items():
        if not entry.runner or entry.kind != "transform":
            continue
        namespace = {**context.__dict__, "candidate": source, "diff": verdict.get("diff"),
                     "initial_verdict": verdict}
        try:
            result = entry.resolve()(namespace, {})
        except Exception as exc:                                # noqa: BLE001
            result = {"status": "crashed", "reason": f"{type(exc).__name__}: {exc}"}
        rows[action] = {"status": result.get("status"), "changed": result.get("changed"),
                        "reason": str(result.get("reason") or "")[:220],
                        "detail_keys": sorted(result.get("detail") or {})}
        print(f"  {action:20} status={str(rows[action]['status']):14} changed={rows[action]['changed']}")
        if rows[action]["reason"]:
            print(f"      reason: {rows[action]['reason']}")
    report[name] = rows

OUT.write_text(json.dumps({"schema_version": 1, "kind": "registry-declines",
                           "note": "Named decline reasons for every registered transform on two states.",
                           "functions": report}, indent=2) + "\n", encoding="utf-8")
print(f"\nwritten {OUT}")
