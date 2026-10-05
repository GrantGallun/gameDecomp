"""Item 2's before/after, and where the new action earned anything.

Reads the two traced runs and the per-step chain, so the answer is not just the conversion count -- a
recovery that fixes the diagnostic but leaves the state blocked is progress the count cannot show, and a
recovery that fires on many states and converts none is worth knowing about too.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
BEFORE = BASE / "wide-intake-traced.json"
AFTER = BASE / "wide-intake-types.json"
DEADLINE = time.time() + 600

while time.time() < DEADLINE:
    try:
        after = json.loads(AFTER.read_text(encoding="utf-8"))
        break
    except (OSError, ValueError):
        time.sleep(15)
else:
    raise SystemExit(f"{AFTER.name} did not appear")

before = json.loads(BEFORE.read_text(encoding="utf-8"))
print(f"{'arm':44} {'conv':>5} {'exact':>6} {'rate':>7}")
for label, payload in (("before (sequence without the new action)", before),
                       ("after  (with source_type_declarations)", after)):
    print(f"{label:44} {payload['sequence_converted']:5} {payload['sequence_exact']:6} "
          f"{payload['conversion_rate']:7.3f}")

sa = {r["function"]: r.get("draft_sha256") for r in before["rows"]}
sb = {r["function"]: r.get("draft_sha256") for r in after["rows"]}
print(f"\nframe identity: same set={set(sa) == set(sb)} differing drafts="
      f"{sum(1 for f in sa if sa[f] != sb.get(f))}")

gained = [r["function"] for r in after["rows"] if r["sequence"]["compiled"]
          and r["function"] not in {x["function"] for x in before["rows"] if x["sequence"]["compiled"]}]
lost = [r["function"] for x in before["rows"] if x["sequence"]["compiled"]
        for r in after["rows"] if r["function"] == x["function"] and not r["sequence"]["compiled"]]
print(f"newly converted : {gained}")
print(f"regressions     : {lost}")

action = after["per_action"].get("eval.intake_runners.source_type_declarations") or {}
print(f"\nthe new action:")
for key in ("fired", "compiled", "exact", "declined", "crashed"):
    print(f"   {key:10} {action.get(key, 0)}")

recovered = []
declined: Counter = Counter()
corroborated_states = carried_states = 0
for row in after["rows"]:
    entry = row["actions"].get("eval.intake_runners.source_type_declarations") or {}
    detail = entry.get("detail") or {}
    if detail.get("corroborated"):
        corroborated_states += 1
    if detail.get("carried"):
        carried_states += 1
    for item in (detail.get("recovered") or []):
        support = ("corroborated" if item["type"] in (detail.get("corroborated") or {})
                   else "carried" if item["type"] in (detail.get("carried") or {})
                   else "?")
        recovered.append((row["function"], item["type"], item.get("parameter"), support))
    for reason in (detail.get("declined") or []):
        declined[reason.split(":")[0] if ":" in reason else reason[:60]] += 1

print(f"\nstates with at least one CORROBORATED recovery : {corroborated_states}")
print(f"states with at least one CARRIED recovery      : {carried_states}")
print(f"\ndeclarations recovered: {len(recovered)}")
for function, type_name, parameter, support in recovered[:22]:
    print(f"   {function:44} {type_name:32} {parameter or '-':8} {support}")
print(f"\nthe commonest declines:")
for reason, count in declined.most_common(10):
    print(f"   {count:4}  {reason[:84]}")

# Did the recovered declarations move the chain, even where they did not convert?
moved = 0
for a, b in zip(before["rows"], after["rows"]):
    fa = (a.get("diagnostic_trace") or [{}])[-1].get("classes") or []
    fb = (b.get("diagnostic_trace") or [{}])[-1].get("classes") or []
    if fa != fb:
        moved += 1
print(f"\nstates whose FINAL defect-class set changed: {moved}")
for a, b in zip(before["rows"], after["rows"]):
    fa = (a.get("diagnostic_trace") or [{}])[-1].get("classes") or []
    fb = (b.get("diagnostic_trace") or [{}])[-1].get("classes") or []
    if fa != fb:
        print(f"   {a['function']:44} {fa} -> {fb}")

(BASE / "type-declaration-receipt.json").write_text(json.dumps(
    {"before": {"converted": before["sequence_converted"], "exact": before["sequence_exact"]},
     "after": {"converted": after["sequence_converted"], "exact": after["sequence_exact"]},
     "newly_converted": gained, "regressions": lost,
     "action": {k: action.get(k, 0) for k in ("fired", "compiled", "exact", "declined", "crashed")},
     "recovered": [{"function": f, "type": t, "parameter": p, "support": s}
                   for f, t, p, s in recovered],
     "declines": dict(declined), "class_sets_changed": moved,
     "note": "declarations only; members the binary corroborates are separated from those carried on the "
             "project's authority"},
    indent=2) + "\n", encoding="utf-8")
print(f"\nwrote {BASE / 'type-declaration-receipt.json'}")
