"""Name the `unclassified` frontier: what clang actually says once the declaration walls are down.

The blocker graph says the three declaration classes gate the rest, and that clearing them reveals
`unclassified` in 49 (state, step) transitions -- a class with no name and, therefore, no owner. A frontier
nobody can name is a frontier nobody can repair, so this reads the actual diagnostics behind it.

Recovers each state's final candidate by the sha256 the probe recorded, runs the real checker, and prints
the distinct messages with counts and the states they came from. No model, no build writes. Run in WSL:

  PYTHONPATH=/mnt/c/Code/gameDecomp python -m eval.results.intake-20260921._unclassified_frontier
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("/mnt/c/Code/gameDecomp")
sys.path.insert(0, str(ROOT))

FRAME = ROOT / "eval/results/intake-20260921/wide-intake-acceptance2.json"
KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"
OUT = ROOT / "eval/results/intake-20260921/unclassified-frontier.json"


def sha(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalise(message: str) -> str:
    """The message with its identifiers and line-specific noise removed, so variants collapse."""
    text = re.sub(r"'[^']*'", "'X'", message)
    text = re.sub(r"\b\d+\b", "N", text)
    return text[:150].strip()


def main() -> int:
    from solver import frontend_diagnostics as frontend
    from eval.tool_agent_run import build_context

    frame = json.loads(FRAME.read_text(encoding="utf-8"))
    targets = [row for row in frame["rows"]
               if set(row["sequence"].get("frontend_classes") or []) == {"unclassified"}]
    print(f"states whose WHOLE residue is the unclassified class: {len(targets)}")

    conn = sqlite3.connect(f"file:{KB}?mode=ro", uri=True)
    messages: Counter = Counter()
    examples: dict = defaultdict(list)
    recovered, failed = 0, []
    try:
        for row in targets[:24]:
            name, digest = row["function"], row["sequence"].get("final_sha256")
            found = conn.execute(
                "select source_code from attempts where source_sha256 = ? and source_code is not null "
                "limit 1", (digest,)).fetchone()
            if not found or sha(found[0]) != digest:
                failed.append({"function": name, "reason": "the recorded bytes are not in the attempt log"})
                continue
            context, why = build_context(REPO, name)
            if context is None:
                failed.append({"function": name, "reason": f"no context: {why}"})
                continue
            report = frontend.analyse(found[0], repo=REPO, target=str(context.target or ""))
            if report["status"] == "unavailable":
                failed.append({"function": name, "reason": report.get("reason")})
                continue
            recovered += 1
            for error in report["errors"]:
                key = normalise(error["what"])
                messages[key] += 1
                if len(examples[key]) < 4:
                    examples[key].append({"function": name, "line": error["line"],
                                          "what": error["what"][:160]})
            print(json.dumps({"function": name, "errors": report["error_count"],
                              "first": (report["errors"][0]["what"][:80] if report["errors"] else None)}),
                  flush=True)
    finally:
        conn.close()

    payload = {"schema_version": 1, "kind": "unclassified-frontier",
               "states_whose_whole_residue_is_unclassified": len(targets),
               "states_examined": recovered, "failed": failed,
               "distinct_messages": [{"normalised": key, "count": count, "examples": examples[key]}
                                     for key, count in messages.most_common(20)],
               "note": ("counts are ERROR LINES, not states. The point of the list is to turn a class with "
                        "no name into named shapes a repair could own.")}
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print()
    print(f"examined {recovered} states, {len(messages)} distinct messages")
    for item in payload["distinct_messages"][:14]:
        print(f"  {item['count']:>3}x  {item['normalised']}")
        for example in item["examples"][:1]:
            print(f"        e.g. {example['function']}: {example['what'][:110]}")
    print(f"\nwritten {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
