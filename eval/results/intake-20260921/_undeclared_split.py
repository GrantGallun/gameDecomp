"""Of the undeclared identifiers blocking 154 states, how many does the repo already know?

The payoff table says `undeclared-identifier` is the WHOLE distance for 22 states and in the way for 154 --
the largest lever by an order of magnitude. Before proposing anything, the class has to be split, because
the two halves have different owners and one of them may already be solved:

  resolvable    a project header declares it, so `header_variant` should be able to pull it in. If these
                states still fail, the mechanism is declining on its own motivating residual.
  m2c-invented  no header declares it. Measured earlier: `CourseGridEntry`, `ALFilter`, `RacePlayer` are
                not in `include/**` at all -- these are structs m2c named and nobody ever wrote down, so no
                amount of header plumbing reaches them. That is `opaque-struct`'s and `binary_types`' job.

Read-only: it reads the run's recorded diagnostics and greps the project's headers.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
REPO = Path.home() / "decomp/sbk1"
INCLUDE = REPO / "include"

UNDECLARED = re.compile(r"use of undeclared identifier '([A-Za-z_]\w*)'|"
                        r"undeclared identifier '([A-Za-z_]\w*)'")
STACK_SLOT = re.compile(r"^sp[0-9A-Fa-f]+$")
# m2c's own flattened member names: a struct member it could not place, so it wrote it as a free name.
MEMBER_LIKE = re.compile(r"^unk[0-9A-Fa-f]+$")

payload = json.loads((BASE / "wide-intake-frontend.json").read_text(encoding="utf-8"))

# One pass over the headers: every identifier that appears in a declaration position anywhere.
declared: set[str] = set()
for header in INCLUDE.rglob("*.h"):
    text = header.read_text(encoding="utf-8", errors="replace")
    declared.update(re.findall(r"\bextern\s+[A-Za-z_][\w \t*]*?([A-Za-z_]\w*)\s*[\[;]", text))
    declared.update(re.findall(r"(?m)^\s*[A-Za-z_][\w \t*]*?\b([A-Za-z_]\w*)\s*\(", text))
    declared.update(re.findall(r"\btypedef\b[^;]*?\b([A-Za-z_]\w*)\s*;", text))

per_state: list[dict] = []
names: Counter = Counter()
for row in payload["rows"]:
    if row["sequence"]["compiled"]:
        continue
    entry = row["actions"].get("eval.intake_runners.frontend_diagnostics") or {}
    detail = entry.get("detail") or {}
    found: list[str] = []
    for error in (detail.get("errors") or []):
        match = UNDECLARED.search(error.get("what") or "")
        if match:
            name = match.group(1) or match.group(2)
            if name not in found:
                found.append(name)
    for name in found:
        names[name] += 1
    per_state.append({"function": row["function"], "tier": row.get("tier"), "names": found})

blocks = [r for r in per_state if r["names"]]
print(f"blocked states                                  : {len(per_state)}")
print(f"  with at least one undeclared identifier       : {len(blocks)}")
print(f"  distinct undeclared names                     : {len(names)}")

kinds: Counter = Counter()
resolvable_states = m2c_only_states = mixed_states = 0
per_name: dict[str, dict] = defaultdict(lambda: {"states": 0, "in_headers": False, "kind": ""})
for row in blocks:
    kinds_here = set()
    for name in row["names"]:
        per_name[name]["states"] += 1
        if STACK_SLOT.match(name):
            kind = "stack-slot (m2c dropped the declaration)"
        elif MEMBER_LIKE.match(name):
            kind = "flattened-member (m2c wrote a struct member as a free name)"
        elif name in declared:
            kind = "DECLARED IN A PROJECT HEADER"
        else:
            kind = "not declared anywhere in include/**"
        per_name[name]["kind"] = kind
        per_name[name]["in_headers"] = per_name[name]["in_headers"] or name in declared
        kinds_here.add(kind)
    if kinds_here == {"DECLARED IN A PROJECT HEADER"}:
        resolvable_states += 1
    elif "DECLARED IN A PROJECT HEADER" in kinds_here:
        mixed_states += 1
    else:
        m2c_only_states += 1

print(f"\nper-state split:")
print(f"  every undeclared name is in a project header  : {resolvable_states}")
print(f"  a mix of both                                 : {mixed_states}")
print(f"  none of them is in a header                   : {m2c_only_states}")

print(f"\nthe names, by how many blocked states each holds back:")
for name, count in names.most_common(28):
    info = per_name[name]
    mark = "HEADER" if info["in_headers"] else "  --  "
    print(f"  {count:4}  {mark}  {name:26} {info['kind']}")

by_kind = Counter(info["kind"] for info in per_name.values())
print(f"\ndistinct names by kind:")
for kind, count in by_kind.most_common():
    print(f"  {count:4}  {kind}")

(BASE / "undeclared-class-split.json").write_text(json.dumps(
    {"blocked": len(per_state), "with_undeclared": len(blocks), "distinct_names": len(names),
     "per_state_kind": {"all_in_headers": resolvable_states, "mixed": mixed_states,
                        "none_in_headers": m2c_only_states},
     "names": {name: {"states": c, **per_name[name]} for name, c in names.items()},
     "by_kind": dict(by_kind), "rows": per_state,
     "note": "read-only: the run's recorded diagnostics joined against the project's headers"},
    indent=2) + "\n", encoding="utf-8")
print(f"\nwrote {BASE / 'undeclared-class-split.json'}")
