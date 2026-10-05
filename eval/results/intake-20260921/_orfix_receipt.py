"""The OR-address fix's before/after, and the sequence of blockers it exposed."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
ARMS = [("wide-intake-traced.json", "1. + frontend chain trace"),
        ("wide-intake-types.json", "2. + source_type_declarations"),
        ("wide-intake-undeclared.json", "3. + undeclared_identifiers"),
        ("wide-intake-voidfix.json", "4. + opaque_variant void fix"),
        ("wide-intake-orfix.json", "5. + or_address")]

loaded = {}
print(f"{'sequence':40} {'conv':>5} {'exact':>6} {'rate':>7}")
for name, label in ARMS:
    path = BASE / name
    if not path.is_file():
        print(f"{label:40} (missing)")
        continue
    data = json.loads(path.read_text(encoding="utf-8"))
    loaded[label] = data
    print(f"{label:40} {data['sequence_converted']:5} {data['sequence_exact']:6} "
          f"{data['conversion_rate']:7.3f}")

newest = loaded.get("5. + or_address")
previous = loaded.get("4. + opaque_variant void fix")
if newest and previous:
    before = {r["function"] for r in previous["rows"] if r["sequence"]["compiled"]}
    after = {r["function"] for r in newest["rows"] if r["sequence"]["compiled"]}
    print(f"\nnewly converted by or_address: {sorted(after - before)}")
    print(f"regressions                  : {sorted(before - after)}")
    for row in newest["rows"]:
        if row["function"] in after - before:
            print(f"   {row['function']:44} score={row['sequence']['score']}")

if newest:
    for name in ("opaque_variant", "or_address"):
        action = newest["per_action"].get(f"eval.intake_runners.{name}") or {}
        print(f"\n{name}: {json.dumps({k: action.get(k, 0) for k in
                                       ('fired', 'compiled', 'exact', 'declined', 'gated')})}")
    blocked = [r for r in newest["rows"] if not r["sequence"]["compiled"]]
    whole: Counter = Counter()
    for row in blocked:
        classes = (row.get("diagnostic_trace") or [{}])[-1].get("classes") or []
        if len(classes) == 1:
            whole[classes[0]] += 1
    print(f"\nblocked: {len(blocked)}; whole-distance: {dict(whole.most_common())}")

(BASE / "orfix-receipt.json").write_text(json.dumps(
    {"arms": {label: {"converted": d["sequence_converted"], "exact": d["sequence_exact"]}
              for label, d in loaded.items()},
     "note": "`*(p | n)` is cast where the reference puts the cast; the operator is unchanged"},
    indent=2) + "\n", encoding="utf-8")
print(f"\nwrote {BASE / 'orfix-receipt.json'}")
