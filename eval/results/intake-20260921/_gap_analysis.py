"""What stands between 29 of 200 and 200 of 200.

The honest answer has four separable parts, and each is measurable from the current run:

  1. THE CHAIN. Every blocked state is a chain of defect classes, not one. If the mean is 2.5, then the
     per-state cost of "all 200" is not the number of classes but the number of LINKS ACROSS ALL STATES.
  2. THE SINGLE-CLASS STATES. A state with one class is one fix from compiling, so the count per class is
     the conversion rate a fix would buy -- and the sum is how far a per-class sweep would get.
  3. WHICH CLASSES ARE MECHANICAL. Some have an owner module that produces a bounded, checkable edit;
     others need a mapping the repository does not have (a LOCAL's home register to an offset), or a
     semantic decision the project's own rules reserve.
  4. THE CODEGEN CEILING. Compiling is not matching. Even among the 29 that compile, exactness is 2. If
     the compiles cap out at 70-95% similarity, then "all 200 compiling" and "all 200 matching" are
     different problems with different costs, and the second is not reachable by repair alone.

Read-only; everything from `wide-intake-orfix.json` plus a header scan.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
REPO = Path.home() / "decomp/sbk1"
payload = json.loads((BASE / "wide-intake-orfix.json").read_text(encoding="utf-8"))

blocked = [r for r in payload["rows"] if not r["sequence"]["compiled"]]
compiled = [r for r in payload["rows"] if r["sequence"]["compiled"]]

print("=" * 78)
print("1. THE CHAIN")
print("=" * 78)
chains = []
for row in blocked:
    classes = (row.get("diagnostic_trace") or [{}])[-1].get("classes") or []
    chains.append(classes)
lengths = Counter(len(c) for c in chains)
for length in sorted(lengths):
    print(f"   {length} class(es): {lengths[length]:4} states")
total_links = sum(len(c) for c in chains)
print(f"   states {len(blocked)}, total class-instances {total_links}, "
      f"mean {total_links / len(blocked):.2f}")

print()
print("=" * 78)
print("2. THE SINGLE-CLASS STATES, and what each class would buy")
print("=" * 78)
whole: Counter = Counter()
for classes in chains:
    if len(classes) == 1:
        whole[classes[0]] += 1
print(f"   states one fix from compiling: {sum(whole.values())} of {len(blocked)}")
for name, count in whole.most_common():
    print(f"     {count:4}  {name}")

# Multi-class states: a fix to one class shortens them but does not convert them.
present: Counter = Counter()
for classes in chains:
    for name in classes:
        present[name] += 1
print(f"\n   `whole distance` vs `in the way`, for the classes that have both:")
print(f"     {'class':28} {'whole':>6} {'present':>8} {'ratio':>7}")
for name in sorted(present, key=lambda n: -present[n]):
    print(f"     {name:28} {whole.get(name, 0):6} {present[name]:8} "
          f"{(whole.get(name, 0) / present[name]):7.2f}")

print()
print("=" * 78)
print("3. WHICH CLASSES ARE MECHANICAL")
print("=" * 78)
# For each whole-distance class, is the blocker a name the project already declares somewhere?
INCLUDE = REPO / "include"
declared: set[str] = set()
for header in INCLUDE.rglob("*.h"):
    text = header.read_text(encoding="utf-8", errors="replace")
    declared.update(re.findall(r"\bextern\s+[A-Za-z_][\w \t*]*?([A-Za-z_]\w*)\s*[\[;(]", text))
    declared.update(re.findall(r"(?m)^\s*[A-Za-z_][\w \t*]*?\b([A-Za-z_]\w*)\s*\(", text))

UNDECLARED = re.compile(r"use of undeclared identifier '([A-Za-z_]\w*)'|"
                        r"undeclared identifier '([A-Za-z_]\w*)'")
STACK = re.compile(r"^sp[0-9A-Fa-f]*$")
names_in_headers = names_not_in_headers = 0
examples_not: list[str] = []
for row, classes in zip(blocked, chains):
    if classes != ["undeclared-identifier"]:
        continue
    entry = row["actions"].get("eval.intake_runners.frontend_diagnostics") or {}
    for error in ((entry.get("detail") or {}).get("errors") or []):
        match = UNDECLARED.search(error.get("what") or "")
        if not match:
            continue
        name = match.group(1) or match.group(2)
        if STACK.match(name):
            continue
        if name in declared:
            names_in_headers += 1
        else:
            names_not_in_headers += 1
            if name not in examples_not:
                examples_not.append(name)
print(f"   `undeclared-identifier` whole-distance states: {whole.get('undeclared-identifier', 0)}")
print(f"     names a project header declares ({names_in_headers})  <- a header-resolution gap")
print(f"     names no header declares      ({names_not_in_headers})  <- e.g. {examples_not[:6]}")

OWNERS = {
    "undeclared-identifier": ("solver/undeclared_identifiers + header_variant + globals_variant",
                              "wired; still the whole distance for these states"),
    "member-on-typed-pointer": ("opaque_variant + m2c_or_address", "wired this session; local-attached types remain unreachable"),
    "unclassified": ("source_type_declarations", "wired this session; 10 states still unnamed"),
    "undeclared-member": ("opaque_variant", "needs a struct whose member set the binary can fix"),
    "redeclaration/conflict": ("project_headers.reconcile_declarations", "this is the 4-state defect I tried and REVERTED"),
    "other-syntax": ("none", "not diagnosed"),
    "incompatible-pointer": ("solver/byte_array_decay / frontend_repair", "gated on clang wording that is now produced"),
}
for name in sorted(whole, key=lambda n: -whole[n]):
    owner, note = OWNERS.get(name, ("none", "no owner"))
    print(f"   {whole[name]:3}  {name}")
    print(f"        owner: {owner}")
    print(f"        state: {note}")

print()
print("=" * 78)
print("4. THE CODEGEN CEILING")
print("=" * 78)
scores = sorted((r["sequence"]["score"] or 0.0) for r in compiled)
print(f"   compiling states: {len(compiled)}, exact: "
      f"{sum(1 for r in compiled if r['sequence']['exact'])}")
print(f"   score distribution: min {scores[0]:.1f}, median {scores[len(scores) // 2]:.1f}, "
      f"max {scores[-1]:.1f}")
bands = Counter()
for score in scores:
    if score >= 100:
        bands["100 (exact or near)"] += 1
    elif score >= 90:
        bands["90-99"] += 1
    elif score >= 75:
        bands["75-89"] += 1
    elif score >= 50:
        bands["50-74"] += 1
    else:
        bands["below 50"] += 1
for band, count in sorted(bands.items(), reverse=True):
    print(f"     {band:22} {count}")
