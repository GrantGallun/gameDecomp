"""What is actually blocking the pending functions in a running campaign, from its own state.

The size table says WHERE the campaign stops (870 work items, 0 exact above 1 KiB). It does not say
WHY, and a fix chosen from the size table alone is a guess. This reads the run's checkpoint and
reports the two things that pick a fix:

  * the terminal COMPILER ERROR class per pending function, normalised so counts aggregate
  * which profiles have already run on those functions, and whether the source hash moved

A class that dominates and has no strategy in `solver/compile_recovery.variants` is a fix target. A
class that dominates and IS covered means the strategy is declining silently, which is a different bug.

    python3 -m eval.stagnation_report --run DIR [--out FILE]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import campaign_state                                        # noqa: E402

# cfe names the file and line; neither is useful for aggregating a class.
LOCATION = re.compile(r"[^,]*candidate\.c,? ?(?:line \d+:)?")
QUOTED = re.compile(r"'[^']*'")


def normalise(line: str) -> str:
    line = LOCATION.sub("", line).strip()
    line = QUOTED.sub("'X'", line)
    line = re.sub(r"\b0x[0-9a-fA-F]+\b", "0xN", line)
    return line[:110]


def classify(node: dict) -> str:
    residual = node.get("residual") or {}
    if residual.get("compiled") is False:
        errors = residual.get("compiler_stderr") or residual.get("errors") or []
        if isinstance(errors, str):
            errors = errors.splitlines()
        classes = [normalise(e) for e in errors if e and e.strip()]
        return classes[0] if classes else "compiled=false-with-no-diagnostic"
    if (residual.get("frontend") or {}).get("passed") is False:
        return "frontend-failed"
    if residual.get("compiled") is True:
        return "compiles-not-exact"
    return "no-residual"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    run = args.run.resolve()
    state = campaign_state.read(run / "campaign.json")
    nodes = state["nodes"]
    status = collections.Counter(n["status"] for n in nodes.values())
    pending = {k: n for k, n in nodes.items() if n["status"] not in {"object_exact", "integrated"}}

    classes = collections.Counter(classify(n) for n in pending.values())
    # Names per class, so a class can be inspected rather than only counted: "61 with no diagnostic"
    # is not actionable until you know whether they are blank drafts, missing symbols, or a backend gap.
    by_class: dict[str, list[str]] = collections.defaultdict(list)
    for name, node in pending.items():
        by_class[classify(node)].append(name)
    residual_shape = collections.Counter(
        "no-residual-key" if not (n.get("residual") or {}) else
        "compiled=" + str((n.get("residual") or {}).get("compiled")) for n in pending.values())
    profiles = collections.Counter(
        job.get("profile") for n in pending.values() for job in (n.get("jobs") or []))
    # A function whose source has not moved across many visits is the stagnation signature: the
    # campaign is spending work without changing the hypothesis.
    stale = sorted(
        (len(n.get("jobs") or []), k) for k, n in pending.items()
        if len({j.get("source_sha256") for j in (n.get("jobs") or [])}) <= 1 and (n.get("jobs") or []))
    stale.sort(reverse=True)

    report = {"run": str(run), "commit": state.get("commit"), "nodes": len(nodes),
              "status": dict(status), "pending": len(pending),
              "terminal_classes": classes.most_common(20),
              "names_by_class": {k: v[:25] for k, v in by_class.items()},
              "residual_shape": dict(residual_shape),
              "profiles_on_pending": profiles.most_common(20),
              "unchanged_source_top": stale[:15],
              "unchanged_source_functions": len(stale)}
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "unchanged_source_top"}, indent=2))
    print("unchanged-source sample:", json.dumps(stale[:10]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
