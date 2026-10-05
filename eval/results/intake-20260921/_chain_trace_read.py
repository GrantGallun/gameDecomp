"""Which step actually shortens the defect chain? Read from the per-step trace.

`chain-analysis.json` read the frontend once, at position 2 of 7, and ranked `undeclared-identifier` as the
top lever -- but `header_variant` at position 4 takes `renderRacePickupRespawn` from 11 undeclared
identifiers to zero. The ranking was of a HEAD, not a distance.

`wide-intake-traced.json` records clang's classes after every step, so the question becomes measurable:
per step, how much of the chain does that step remove, and where does it stop?
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
payload = json.loads((BASE / "wide-intake-traced.json").read_text(encoding="utf-8"))

steps = [step.split(".")[-1] for step in
         ("resolve_placeholders", "negative_offset", "header_variant", "globals_variant",
          "opaque_variant", "rewrite_do_while")]
traced = [r for r in payload["rows"] if r.get("diagnostic_trace")]
print(f"states with a trace: {len(traced)} of {len(payload['rows'])}\n")

# Per step: mean class count before and after, over the states that reach that step.
before_after: dict[str, list[tuple[int, int]]] = defaultdict(list)
for row in traced:
    trace = row["diagnostic_trace"]
    for earlier, later in zip(trace, trace[1:]):
        if later["after"] == "final":
            continue
        before_after[later["after"]].append(
            (len(earlier.get("classes") or []), len(later.get("classes") or [])))

print(f"{'step':22} {'n':>4} {'classes before':>15} {'classes after':>14} {'removed':>8}")
for step in steps:
    pairs = before_after.get(step) or []
    if not pairs:
        print(f"{step:22} {0:4}  (never reached with a trace)")
        continue
    before = sum(a for a, _ in pairs) / len(pairs)
    after = sum(b for _, b in pairs) / len(pairs)
    print(f"{step:22} {len(pairs):4} {before:15.2f} {after:14.2f} {before - after:8.2f}")

# Where the chain ENDS: the class set on the final candidate, for the still-blocked states.
final_sets = Counter()
for row in traced:
    if row["sequence"]["compiled"]:
        continue
    final = row["diagnostic_trace"][-1]
    final_sets[tuple(sorted(final.get("classes") or []))] += 1

print(f"\nthe final chain, over the {sum(final_sets.values())} still-blocked traced states:")
for classes, count in final_sets.most_common(12):
    label = " + ".join(classes) if classes else "(clang reports no error)"
    print(f"  {count:4}  {label[:96]}")

lengths = Counter(len(classes) for classes in final_sets.elements())
print(f"\nfinal chain length:")
for length in sorted(lengths):
    print(f"  {length:2} class(es): {lengths[length]:4} states")

single = [classes[0] for classes in final_sets.elements() if len(classes) == 1]
print(f"\nwhole-distance classes, read at the END this time:")
for name, count in Counter(single).most_common():
    print(f"  {count:4}  {name}")

(BASE / "chain-trace.json").write_text(json.dumps(
    {"states_traced": len(traced),
     "per_step": {step: {"n": len(before_after.get(step) or []),
                         "mean_classes_before": (sum(a for a, _ in before_after[step]) /
                                                 len(before_after[step]))
                         if before_after.get(step) else None,
                         "mean_classes_after": (sum(b for _, b in before_after[step]) /
                                                len(before_after[step]))
                         if before_after.get(step) else None}
                  for step in steps},
     "final_chain_lengths": {str(k): v for k, v in sorted(lengths.items())},
     "whole_distance": dict(Counter(single)),
     "note": "read from --trace-frontend: clang after every step"},
    indent=2) + "\n", encoding="utf-8")
print(f"\nwrote {BASE / 'chain-trace.json'}")
