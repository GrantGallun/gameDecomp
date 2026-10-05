"""The void fix's before/after, and what the next blocker actually is."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
ARMS = [("wide-intake-traced.json", "1. + frontend chain trace"),
        ("wide-intake-types.json", "2. + source_type_declarations"),
        ("wide-intake-undeclared.json", "3. + undeclared_identifiers"),
        ("wide-intake-voidfix.json", "4. + the opaque_variant void fix")]

loaded = {}
print(f"{'sequence':44} {'conv':>5} {'exact':>6} {'rate':>7}")
for name, label in ARMS:
    path = BASE / name
    if not path.is_file():
        print(f"{label:44} (missing)")
        continue
    data = json.loads(path.read_text(encoding="utf-8"))
    loaded[label] = data
    print(f"{label:44} {data['sequence_converted']:5} {data['sequence_exact']:6} "
          f"{data['conversion_rate']:7.3f}")

newest = loaded.get("4. + the opaque_variant void fix")
if newest and "3. + undeclared_identifiers" in loaded:
    before = {r["function"] for r in loaded["3. + undeclared_identifiers"]["rows"]
              if r["sequence"]["compiled"]}
    after = {r["function"] for r in newest["rows"] if r["sequence"]["compiled"]}
    print(f"\nnewly converted by the void fix: {sorted(after - before)}")
    print(f"regressions                    : {sorted(before - after)}")
    exact_before = {r["function"] for r in loaded["3. + undeclared_identifiers"]["rows"]
                    if r["sequence"]["exact"]}
    exact_after = {r["function"] for r in newest["rows"] if r["sequence"]["exact"]}
    print(f"newly EXACT                    : {sorted(exact_after - exact_before)}")

if newest:
    action = newest["per_action"].get("eval.intake_runners.opaque_variant") or {}
    print(f"\nopaque_variant: {json.dumps({k: action.get(k, 0) for k in
                                       ('fired', 'compiled', 'exact', 'declined', 'gated')})}")
    blocked = [r for r in newest["rows"] if not r["sequence"]["compiled"]]
    whole: Counter = Counter()
    for row in blocked:
        classes = (row.get("diagnostic_trace") or [{}])[-1].get("classes") or []
        if len(classes) == 1:
            whole[classes[0]] += 1
    print(f"blocked: {len(blocked)}; whole-distance classes: {dict(whole.most_common())}")

(BASE / "voidfix-receipt.json").write_text(json.dumps(
    {"arms": {label: {"converted": d["sequence_converted"], "exact": d["sequence_exact"]}
              for label, d in loaded.items()},
     "note": "the void fix lets opaque_variant emit a derived struct tag AND respell the parameter; "
             "measured on the frozen 200"},
    indent=2) + "\n", encoding="utf-8")
print(f"\nwrote {BASE / 'voidfix-receipt.json'}")
