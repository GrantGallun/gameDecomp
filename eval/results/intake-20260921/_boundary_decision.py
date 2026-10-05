"""The deduplicated boundary census, and the promotion decision it forces.

The first version counted RECEIPTS (2,720 files, one per compile attempt, several per function), so a
function compiled nine times appeared nine times. The unit that matters is the function, and the question
is whether its bytes certify even though the object-level certificate failed.

Then the decision. Three candidate answers, and the difference between them is what a promotion would mean:

  (a) widen `Attempt.exact`      -- NO. It drives the ratchet, and the boundary certificate excludes the
                                    whole ROM and declares `requires_isolated_integration: True` on purpose.
  (b) report them separately     -- these functions are already promoted through the campaign path
                                    (`completion_campaign.py:210` sets `function_exact_pending_integration`,
                                    and `prepare_integration` revalidates the boundary before integrating)
  (c) they are failures          -- only if integration is the test, which for the ROM it is

So the answer depends on whether this class is ALREADY counted somewhere. That is checkable: the campaign
state files and the KB record statuses, and a function sitting in `function_exact_pending_integration` is
waiting on integration rather than unsolved. This prints the class, then checks where they stand.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
REPO = Path.home() / "decomp/sbk1"
KB = Path.home() / "decomp/kb-sbk1.sqlite"

census = json.loads((BASE / "boundary-census.json").read_text(encoding="utf-8"))
rows = census["rows"]

by_function: dict[str, dict] = {}
for row in rows:
    current = by_function.get(row["function"])
    if current is None or (row["boundary_exact"] and not current["boundary_exact"]):
        by_function[row["function"]] = row

functions = sorted(by_function.values(), key=lambda r: r["function"])
passed = [r for r in functions if r["boundary_exact"] and not r["object_exact"]]
print(f"functions with a stored verification receipt : {len(functions)}")
print(f"object certificate exact                     : {sum(1 for r in functions if r['object_exact'])}")
print(f"boundary certificate exact                   : {sum(1 for r in functions if r['boundary_exact'])}")
print(f"boundary exact AND object exact FALSE        : {len(passed)}")
print(f"\nthe class ({len(passed)} functions):")
for row in passed:
    print(f"  {row['function']:44} {row['boundary_status']}")

# Are any of them ALREADY integrated, i.e. counted as matches through the campaign path?
print("\nare they already counted anywhere?")
conn = sqlite3.connect(f"file:{KB}?mode=ro", uri=True)
try:
    states = {}
    for row in passed:
        found = conn.execute("select state, best_score from functions where name = ?",
                             (row["function"],)).fetchone()
        states[row["function"]] = found
    for name, found in states.items():
        print(f"  {name:44} functions.state={found[0] if found else '(absent)'} "
              f"best_score={found[1] if found else '-'}")
    matched = sum(1 for f in states.values() if f and f[0] == "matched")
    print(f"\n  of the {len(passed)}, {matched} are marked `matched` in the KB")
finally:
    conn.close()

# And is the source in the real build path? If it is not, the function is not a match by the project's own
# definition, whatever any certificate says.
print("\nare any of them in the build path (src/)?")
found_in_src = []
for row in passed:
    result = subprocess.run(["grep", "-rl", "--include=*.c", f"{row['function']}(", str(REPO / "src")],
                            capture_output=True, text=True)
    if result.stdout.strip():
        found_in_src.append((row["function"], result.stdout.strip().splitlines()[0]))
for name, path in found_in_src:
    print(f"  {name:44} {Path(path).relative_to(REPO)}")
if not found_in_src:
    print("  none: these functions are not in the build path, so they are not matches by the project's "
          "own definition")

(BASE / "boundary-decision.json").write_text(json.dumps(
    {"functions_with_receipts": len(functions),
     "object_exact": sum(1 for r in functions if r["object_exact"]),
     "boundary_exact": sum(1 for r in functions if r["boundary_exact"]),
     "boundary_exact_object_not_exact": len(passed),
     "class": [r["function"] for r in passed],
     "in_build_path": [name for name, _ in found_in_src],
     "decision": {
         "widen_attempt_exact": "NO",
         "why": ("the boundary certificate compares annotated function bytes and external call "
                 "relocations only; it excludes trailing text bytes, TU/link layout and the whole ROM, and "
                 "declares requires_isolated_integration. `Attempt.exact` gates the ratchet, so widening "
                 "it on this receipt would count as matches functions that have not been integrated."),
         "what_they_are": ("a promotion-pending class that the campaign path already handles "
                           "(`completion_campaign.py:210` -> function_exact_pending_integration -> "
                           "`prepare_integration` revalidates the boundary before integrating). The "
                           "measurement gap is that harnesses keyed on `attempt.exact` -- this session's "
                           "probes included -- cannot see them, so a probe's 'certified matches' is a "
                           "lower bound rather than the count of promotable functions."),
         "action_taken": ("none to the code path; the class is named and counted so a later reader does "
                          "not rediscover `rmonPrintf` as an anomaly"),
     },
     "note": "read-only census over stored receipts plus a KB read and a grep of the build path"},
    indent=2) + "\n", encoding="utf-8")
print(f"\nwrote {BASE / 'boundary-decision.json'}")
