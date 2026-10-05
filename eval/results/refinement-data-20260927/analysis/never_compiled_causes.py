"""Why do the 28 never-compiled functions fail? Classify every logged replay attempt by its compiler
errors, per function. Read-only (admission replay KB). The question it answers: are these failures one
root cause per function that a deterministic repair owns, or open-ended code errors a model must fix?

    python3 never_compiled_causes.py
"""
import collections
import re
import sqlite3

db = sqlite3.connect("file:/home/grant/decomp/experiments/admission-replay-20260927/replay.sqlite?mode=ro",
                     uri=True)
CLASSES = [
    ("m2c '?' placeholder type", lambda src, e: bool(re.search(r"^\s*(extern\s+)?\?[\s*]", src, re.M))),
    ("pointer arithmetic / operand type ('Unacceptable operand')", lambda s, e: "Unacceptable operand" in e),
    ("member access on a non-struct ('Selector requires')", lambda s, e: "Selector requires" in e),
    ("undefined identifier", lambda s, e: "undefined" in e.lower()),
    ("redeclaration / conflicting type", lambda s, e: "redeclaration" in e or "conflicting" in e.lower()
     or "incompatible" in e.lower()),
    ("syntax error", lambda s, e: "Syntax Error" in e),
    ("other", lambda s, e: True),
]

per_function = collections.defaultdict(collections.Counter)
first_error = {}
for name, strategy, src, err in db.execute(
        "select f.name, a.strategy, a.source_code, coalesce(a.compiler_stderr,'') from attempts a "
        "join functions f on f.addr=a.func_addr where a.run_id='never-compiled' and coalesce(a.compiled,0)=0 "
        "order by a.id"):
    if "original" not in strategy:                 # classify the drafts as intake produced them
        continue
    errors = "\n".join(l for l in err.splitlines() if "Error" in l)
    hits = [label for label, test in CLASSES if test(src or "", errors)]
    per_function[name][hits[0]] += 1
    first_error.setdefault(name, re.sub(r"line \d+", "line N", (errors.splitlines() or [err[:150]])[0])[:150])
dominant = collections.Counter(c.most_common(1)[0][0] for c in per_function.values())
print(f"functions with logged intake drafts: {len(per_function)}")
print("dominant cause per function (the first matching class for each draft, most common per function):")
for label, n in dominant.most_common():
    print(f"  {n:3d}  {label}")
uniform = sum(1 for c in per_function.values() if len(c) == 1)
print(f"functions whose drafts all fail the same way: {uniform}/{len(per_function)}")
print("\nper function:")
for name, c in sorted(per_function.items()):
    print(f"  {name:44s} {dict(c)}  | {first_error[name]}")
