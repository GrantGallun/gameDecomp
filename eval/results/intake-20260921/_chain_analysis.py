"""How long is the defect chain per state, and which class actually converts states?

WHY THE FIRST-ERROR CENSUS COULD NOT ANSWER THIS. Every ranking in this session has been built from the
FIRST error cfe reported, and cfe truncates its list at that first one. That quantity cannot distinguish:

    one defect left          -> fixing it converts the state
    one defect first of six  -> fixing it converts nothing

so "8 states fail on `->unk-N`" was read as "8 states are one fix away", and two fixes later the
conversion count had not moved. `negative_offset` fired on 3, not 8: the redeclarations were in front of
it. The same mistake is available for every class.

THE ALTERNATIVE IS NOW AVAILABLE. The clang step reports EVERY independent blocker in one pass, so a state
carries a CHAIN of classes rather than a head. From that:

    single-class states   -- exactly one defect class: these are the states a fix can actually convert
    the distribution      -- how many classes a state carries on average, which is the theory's missing
                             parameter
    per-class payoff      -- how many states would convert if that class, and only that class, were solved

and the honest version of "can tools fix this": for each class, how many states is it the WHOLE distance
for, versus how many is it merely first in line.

Read-only: derived entirely from `wide-intake-frontend.json`.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
payload = json.loads((BASE / "wide-intake-frontend.json").read_text(encoding="utf-8"))

# Ordered: the first pattern that matches a diagnostic names its class. The labels are the REPAIR OWNER
# where one exists, so a payoff can be read as "this many states are `opaque-struct`'s whole distance".
CLASSES: tuple[tuple[str, re.Pattern], ...] = (
    ("negative-field-offset", re.compile(r"member reference base type")),
    ("undeclared-identifier", re.compile(r"use of undeclared identifier|undeclared identifier")),
    ("unknown-type-name", re.compile(r"unknown type name")),
    ("undeclared-function", re.compile(r"implicit declaration of function")),
    ("redeclaration/conflict", re.compile(r"redeclaration|conflicting types|previous declaration")),
    ("incompatible-pointer", re.compile(r"incompatible pointer types")),
    ("parameter-declarator", re.compile(r"expected parameter declarator|type name requires a specifier")),
    ("expected-identifier", re.compile(r"expected identifier or '\('")),
    ("not-a-struct-member", re.compile(r"not a structure or union")),
    ("m2c-macro", re.compile(r"M2C_FIELD|M2C_UNALIGNED|M2C_MEMCPY_ALIGNED|M2C_UNK")),
    ("other-syntax", re.compile(r"syntax|expected|invalid|illegal", re.I)),
)


def classify(message: str) -> str:
    for label, pattern in CLASSES:
        if pattern.search(message):
            return label
    return "unclassified"


def chain(row: dict) -> dict:
    entry = row["actions"].get("eval.intake_runners.frontend_diagnostics") or {}
    detail = entry.get("detail") or {}
    errors = detail.get("errors") or []
    classes = []
    for error in errors:
        name = classify(error.get("what") or "")
        if name not in classes:
            classes.append(name)
    return {"function": row["function"], "tier": row.get("tier"),
            "compiled": bool(row["sequence"]["compiled"]),
            "exact": bool(row["sequence"]["exact"]),
            "recorded_errors": len(errors),
            "classes": classes,
            "capped": len(errors) >= 40,
            "entry_status": entry.get("status")}


rows = [chain(row) for row in payload["rows"]]
blocked = [r for r in rows if not r["compiled"]]
converted = [r for r in rows if r["compiled"]]

print(f"states {len(rows)}   compiled {len(converted)}   blocked {len(blocked)}")
print(f"blocked states whose recorded error list hit the 40-error cap: "
      f"{sum(1 for r in blocked if r['capped'])} "
      f"-> their chains are LOWER BOUNDS")

print("\nCHAIN LENGTH over the blocked states (distinct classes per state):")
lengths = Counter(len(r["classes"]) for r in blocked)
for length in sorted(lengths):
    print(f"  {length:2} class(es): {lengths[length]:4} states")
sizes = [len(r["classes"]) for r in blocked]
if sizes:
    print(f"  mean {sum(sizes) / len(sizes):.2f}, median "
          f"{sorted(sizes)[len(sizes) // 2]}")

single = [r for r in blocked if len(r["classes"]) == 1]
print(f"\nSINGLE-CLASS states -- one defect from compiling: {len(single)} of {len(blocked)} blocked")
for row in single:
    print(f"    {row['function']:44} {row['tier']:7} {row['classes'][0]}")

print("\nPER-CLASS PAYOFF. `whole distance` = states where it is the only class left, so solving it "
      "converts them.\n`in the way` = states where it appears at all.")
payoff: dict[str, dict] = defaultdict(lambda: {"whole": 0, "present": 0})
for row in blocked:
    for name in row["classes"]:
        payoff[name]["present"] += 1
    if len(row["classes"]) == 1:
        payoff[row["classes"][0]]["whole"] += 1
print(f"  {'class':26} {'whole distance':>14} {'in the way':>11} {'conversion if solved':>21}")
for name in sorted(payoff, key=lambda n: -payoff[n]["whole"]):
    whole, present = payoff[name]["whole"], payoff[name]["present"]
    ratio = f"{100 * whole / present:.0f}% of those" if present else "-"
    print(f"  {name:26} {whole:14} {present:11} {ratio:>21}")

# The greedy view: solving classes one at a time, cheapest first, and what that actually buys.
print("\nORDERED BY WHAT IT BUYS (whole-distance count), which is the ranking the first-error census "
      "could not produce:")
for name in sorted(payoff, key=lambda n: -payoff[n]["whole"]):
    if payoff[name]["whole"]:
        print(f"  {payoff[name]['whole']:4} states  {name}")

print(f"\nthe classes that are NEVER the whole distance for any state:")
stuck = [name for name in payoff if not payoff[name]["whole"] and payoff[name]["present"]]
for name in sorted(stuck, key=lambda n: -payoff[n]["present"]):
    print(f"  {payoff[name]['present']:4} states have it, 0 would convert  {name}")

(BASE / "chain-analysis.json").write_text(json.dumps(
    {"states": len(rows), "converted": len(converted), "blocked": len(blocked),
     "capped_chains": sum(1 for r in blocked if r["capped"]),
     "chain_lengths": {str(k): v for k, v in sorted(lengths.items())},
     "mean_chain": (sum(sizes) / len(sizes)) if sizes else None,
     "single_class_states": [r["function"] for r in single],
     "payoff": {name: payoff[name] for name in payoff},
     "rows": rows,
     "note": ("classes are derived from clang's own diagnostics, not from the repair modules' gates; a "
              "chain is a lower bound where the 40-error cap was hit")},
    indent=2) + "\n", encoding="utf-8")
print(f"\nwrote {BASE / 'chain-analysis.json'}")
