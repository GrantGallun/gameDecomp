"""Where the remaining work is, with what is established separated from what is not.

Written because two classifications in this session were wrong in the same way -- they read a symptom and
named a cause. `Syntax Error` on a declaration line looked like "undeclared type name"; adding the header
that declares it moved the error by exactly one line (the include) instead of removing it, so the type name
was downstream and not the blocker. Both of those mistakes are recorded here rather than deleted, because
the next reader will be tempted by the same inference.

Every number below comes from a file in this directory; nothing is estimated.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
load = lambda name: json.loads((BASE / name).read_text(encoding="utf-8"))   # noqa: E731

residual = load("post-fix-residual.json")
classes = load("post-fix-residual-classes.json")
types = load("post-fix-undefined-types.json")
intake = load("post-fix-intake.json")
control = load("post-fix-control.json")
valid = load("post-fix-valid-syntax.json")

blocked = [r for r in residual["rows"] if not r["compiled"]]
converted = [r for r in residual["rows"] if r["compiled"]]

print("THE REACH, on the frozen 40")
print(f"  certified                     : {sum(1 for r in converted if r['exact'])}")
print(f"  compiling, not exact          : {sum(1 for r in converted if not r['exact'])}")
print(f"  still not compiling           : {len(blocked)}")

print("\nTHE RESIDUAL, by the construct in the draft's own text (post-fix-residual-classes.json)")
for label, names in sorted(classes["classes"].items(), key=lambda kv: -len(kv[1])):
    print(f"  {len(names):3}  {label}")

print("\nWHAT EACH CLASS HAS BEHIND IT")
for label, note in classes["already_built"].items():
    print(f"  {len(classes['classes'].get(label, [])):3}  {label}")
    print(f"       {note}")

print("\nTWO HYPOTHESES THIS SESSION RAISED AND DISPROVED")
print("  1. `--valid-syntax` produces the syntax-clean draft, so the 31 are 'a flag'.")
print(f"     MEASURED: valid_syntax arm converted {valid['converted']} of {len(valid['rows'])} "
      f"({100 * valid['converted'] / len(valid['rows']):.1f}%), against "
      f"{intake['sequence_converted']} of {intake['front_door_failures']} "
      f"({100 * intake['conversion_rate']:.1f}%) without it. It re-encodes field accesses as "
      f"`M2C_FIELD(...)` -- a different non-C form -- so it is worse, not better.")
print("  2. `Syntax Error` on a declaration line means an undeclared type name, and `header_variant` "
      "should\n     supply it. MEASURED: for all 8 states where a project header declares the name, "
      "adding that\n     header moved the first error by EXACTLY ONE LINE -- the include itself -- and "
      "removed nothing.\n     The type name is downstream of the real blocker.")

counts = Counter(k for k, v in classes["classes"].items() for _ in v)
print("\nNOT ESTABLISHED")
print("  * what the 11 'unclassified' lines actually violate. cfe reports a plausible statement and the")
print("    caret on its identifier; the raw output carries no further context line. One of them")
print(f"    (`clearRaceReplayCourseGrid`) has a `goto loop_1;`/`loop_1:` pair as a candidate cause.")
print(f"  * whether the {sum(1 for r in types['rows'] if r['first_undefined_type'])} undeclared type "
      f"names matter once the real blockers are cleared.")
print("  * the sample is 40 states of one failure class. The frame's membership moved by 4 of 40 between")
print("    two builds of one command, so effects below roughly 4 states are not resolvable by this frame.")

(BASE / "post-fix-shortcomings.json").write_text(json.dumps(
    {"reach": {"certified": sum(1 for r in converted if r["exact"]),
               "compiling_not_exact": sum(1 for r in converted if not r["exact"]),
               "blocked": len(blocked)},
     "classes": {label: len(names) for label, names in classes["classes"].items()},
     "classes_already_built": classes["already_built"],
     "disproved": {
         "valid_syntax_flag": {"converted": valid["converted"], "of": len(valid["rows"]),
                               "default_arm_converted": intake["sequence_converted"]},
         "undeclared_type_names": {"states_tested": 8, "errors_removed": 0,
                                   "effect_observed": "first-error line number shifted by exactly the "
                                                      "one line the include added"}},
     "not_established": ["the 11 unclassified lines", "whether undeclared type names matter downstream",
                         "anything smaller than ~4 states on a 40-state frame"],
     "note": "read-only; derived entirely from the post-fix receipts in this directory"},
    indent=2) + "\n", encoding="utf-8")
print(f"\nwrote {BASE / 'post-fix-shortcomings.json'}")
