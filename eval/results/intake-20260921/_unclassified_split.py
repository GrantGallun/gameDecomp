"""What is actually in the `unclassified` class -- 14 states, the second-largest whole distance?

A class named `unclassified` is a statement about the classifier, not about the code. Before it can be
ranked or owned it has to be split into the things it is really made of, and the first split is the one
that has already mattered twice in this session:

    the class is NAMED where it appears   e.g. `Syntax Error` on `goto loop_1;` -- a real, local construct
    the class is NOT named                the caret is on a plausible statement with no visible defect

The second group is where `unclassified` is doing real work, and it is also where a defect in the SCAN
would hide -- this round already produced one such mistake (a member-declaration scan that missed
typedef'd struct members and reported 149 names as "not declared anywhere"). So this prints the messages
verbatim, grouped, rather than counting them again.

Read-only.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
traced = json.loads((BASE / "wide-intake-traced.json").read_text(encoding="utf-8"))
chain = json.loads((BASE / "chain-trace.json").read_text(encoding="utf-8"))

targets = [row for row in traced["rows"]
           if not row["sequence"]["compiled"]
           and (row.get("diagnostic_trace") or [{}])[-1].get("classes") == ["unclassified"]]
print(f"states whose FINAL chain is exactly ['unclassified']: {len(targets)}")
print(f"(chain-trace.json reports {chain['whole_distance'].get('unclassified', 0)})\n")

messages: Counter = Counter()
by_state: dict[str, list[str]] = defaultdict(list)
for row in targets:
    final = row["diagnostic_trace"][-1]
    # The trace keeps classes; the messages live in the action's detail from the traced read.
    entry = row["actions"].get("eval.intake_runners.frontend_diagnostics") or {}
    detail = entry.get("detail") or {}
    for error in (detail.get("errors") or []):
        text = (error.get("what") or "").strip()
        messages[text[:74]] += 1
        by_state[row["function"]].append(f"{error.get('line')}:{error.get('column')} {text[:64]}")

print("the recorded diagnostics for these states, by message:")
for text, count in messages.most_common(20):
    print(f"  {count:4}  {text}")

print("\nper state, first three diagnostics:")
for row in targets:
    name = row["function"]
    print(f"\n  {name}  ({row.get('tier')})")
    for line in by_state[name][:3]:
        print(f"     {line}")

# Where does the classifier's fallthrough actually catch? Print the regexes that did NOT match, so the
# next person can extend the taxonomy from evidence rather than from another guess.
OTHER = re.compile(r"syntax|expected|invalid|illegal", re.I)
not_by_fallthrough = [text for text in messages if not OTHER.search(text)]
print(f"\nmessages caught by the `other-syntax` fallthrough: "
      f"{sum(c for t, c in messages.items() if OTHER.search(t))}")
print(f"messages NOT matched by any class regex   : "
      f"{sum(c for t, c in messages.items() if not OTHER.search(t))}")
for text in not_by_fallthrough[:12]:
    print(f"     {text}")
