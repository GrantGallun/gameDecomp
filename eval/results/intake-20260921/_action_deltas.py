"""What does `redraft` actually change, and what does `header_variant` actually add?

Both actions "fired" on this frame and the answer to "what did it do" decides how the control is read:

  redraft        fired on 40 of 40 states. Its runner re-runs m2c with no `--context`; the draft the
                 context starts from is ALSO m2c's output. If the delta is a header line or a comment,
                 then "redraft changed the source on every state" is true and means nothing, and any
                 count of improvements attributed to it needs the delta in hand.
  header_variant fired on 31 of 40 and converted none, while on the earlier SMALL frame it was the only
                 mechanism that converted anything (3 of 12, 1 exact). If it runs, reports `changed`, and
                 adds nothing that the compiler was missing, the aggregate hides a mechanism that
                 cannot fire on its motivating residual -- the failure mode CLAUDE.md names.
"""
from __future__ import annotations

import difflib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_control import error_classes                       # noqa: E402
from eval.intake_runners import header_variant                      # noqa: E402
from eval.tool_agent_run import build_context                       # noqa: E402
from eval.tool_runners import redraft                               # noqa: E402

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"

payload = json.loads((BASE / "class-control.json").read_text(encoding="utf-8"))
tiers = {r["function"]: r["tier"] for r in payload["rows"]}


def show_delta(label: str, before: str, after: str, limit: int = 14) -> None:
    diff = list(difflib.unified_diff(before.splitlines(), after.splitlines(),
                                     "before", "after", lineterm="", n=1))
    print(f"    {label}: {len(diff)} diff lines, {len(before)} -> {len(after)} chars")
    for line in diff[:limit]:
        print(f"      {line[:150]}")
    if len(diff) > limit:
        print(f"      ... {len(diff) - limit} more")


SAMPLE = ["__osPopThread", "copyGfxCommandBlockToScratch", "initMenuAssetHandles",
          "updateRaceResultsFlow", "updateRacePlayerGroundAlignment"]

conn = sqlite3.connect(str(KB))
try:
    for name in SAMPLE:
        context, why = build_context(REPO, name, conn=conn)
        if context is None:
            print(f"{name}: {why}")
            continue
        print("=" * 78)
        print(f"{name}  ({tiers.get(name)})")
        print("=" * 78)

        result = redraft({**context.__dict__, "kb_conn": conn}, {})
        changed = bool(result.get("changed"))
        print(f"  redraft: status={result.get('status')} changed={changed} "
              f"produced={len(result.get('source') or '')} chars")
        if changed:
            show_delta("redraft delta", context.candidate, result["source"])
            verdict = context.compile_fn(result["source"])
            base_errors, _ = error_classes((context.initial_verdict or {}).get("stderr") or "")
            now, _ = error_classes(verdict.get("stderr") or "")
            print(f"    compiled={verdict.get('compiled')} errors {base_errors} -> {now}")

        result = header_variant({**context.__dict__, "kb_conn": conn}, {})
        changed = bool(result.get("changed"))
        print(f"  header_variant: status={result.get('status')} changed={changed} "
              f"entry={result.get('entry')}")
        if changed:
            show_delta("header delta", context.candidate, result["source"])
            verdict = context.compile_fn(result["source"])
            base_errors, _ = error_classes((context.initial_verdict or {}).get("stderr") or "")
            now, _ = error_classes(verdict.get("stderr") or "")
            print(f"    compiled={verdict.get('compiled')} errors {base_errors} -> {now}")
        else:
            print(f"    reason={str(result.get('reason'))[:140]}")
        print(f"  detail: {json.dumps(result.get('detail') or {})[:300]}")
finally:
    conn.close()
