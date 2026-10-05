"""The ratchet between two frozen-frame receipts: acceptance AND candidate quality.

WHY ACCEPTANCE ALONE IS NOT A RATCHET. The intake loop adopts a candidate when
`rank = (exact, compiled, score)` is at least as good, so a pass that makes a candidate WORSE on the
frontend -- more errors, still failing -- is adopted on a tie and moves no acceptance level. LOOP-6 is
the measured case: `implicit_externs` fired on 16 states and left IDO / IDO+frontend / exact exactly
flat, while 12 states' final candidates got worse (total frontend errors 3118 -> 3134) and none got
better. Every later pass then reasons about the degraded candidate. Acceptance could not see it.

So this checks both, and exits non-zero on EITHER:

  * any state LOST at any acceptance level (the original ratchet), or
  * any state whose final candidate carries MORE frontend errors than before.

A class count is still not a distance -- `FAULT-HISTOGRAM.md` is the evidence for that -- but an error
count that RISES on the same frozen state, with nothing else changed, is a regression by any reading.

Usage:
    python -m eval.quality_ratchet BEFORE.json AFTER.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

LEVELS = ("compiled", "frontend_passed", "exact")


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _state(receipt: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in receipt.get("rows") or []:
        sequence = row.get("sequence") or {}
        trace = row.get("diagnostic_trace") or []
        out[row["function"]] = {
            **{level: bool(sequence.get(level)) for level in LEVELS},
            "errors": (trace[-1].get("errors") or 0) if trace else None,
        }
    return out


def compare(before: dict, after: dict) -> dict:
    a, b = _state(before), _state(after)
    common = sorted(set(a) & set(b))
    report: dict = {"states": len(common), "levels": {}, "worse": [], "better": [],
                    "errors_before": 0, "errors_after": 0}
    for level in LEVELS:
        gained = [n for n in common if b[n][level] and not a[n][level]]
        lost = [n for n in common if a[n][level] and not b[n][level]]
        report["levels"][level] = {"before": sum(a[n][level] for n in common),
                                   "after": sum(b[n][level] for n in common),
                                   "gained": gained, "lost": lost}
    for n in common:
        ea, eb = a[n]["errors"], b[n]["errors"]
        if ea is None or eb is None:
            continue
        report["errors_before"] += ea
        report["errors_after"] += eb
        if eb > ea:
            report["worse"].append((n, ea, eb))
        elif eb < ea:
            report["better"].append((n, ea, eb))
    report["holds"] = (not any(v["lost"] for v in report["levels"].values())
                       and not report["worse"])
    return report


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1].strip())
        return 2
    report = compare(_load(argv[0]), _load(argv[1]))
    for level, v in report["levels"].items():
        delta = v["after"] - v["before"]
        print(f"{level:16} {v['before']:4} -> {v['after']:4}  ({delta:+d})"
              f"  gained {len(v['gained'])}  LOST {len(v['lost'])} {v['lost'] or ''}")
    print(f"frontend errors  {report['errors_before']} -> {report['errors_after']}"
          f"  worse {len(report['worse'])}  better {len(report['better'])}")
    for name, ea, eb in report["worse"][:12]:
        print(f"   WORSE  {name}: {ea} -> {eb}")
    print("RATCHET HOLDS" if report["holds"] else "RATCHET BROKEN")
    return 0 if report["holds"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
