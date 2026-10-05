"""Functions, not drafts: how many never got ANY compiling candidate, and what blocks them? Read-only."""
import collections
import re
import sqlite3
import sys

db = sqlite3.connect(f"file:{sys.argv[1] if len(sys.argv) > 1 else 'campaign.sqlite'}?mode=ro",
                     uri=True)
per = collections.defaultdict(lambda: {"intake": 0, "intake_ok": 0, "any_ok": 0, "exact": 0,
                                       "errors": collections.Counter()})
for name, strategy, ok, ex, err in db.execute(
        "select f.name, a.strategy, coalesce(a.compiled,0), coalesce(a.exact,0), a.compiler_stderr "
        "from attempts a join functions f on f.addr=a.func_addr"):
    p = per[name]
    p["any_ok"] += ok
    p["exact"] += ex
    if (strategy or "").startswith("campaign-intake"):
        p["intake"] += 1
        p["intake_ok"] += ok
        if not ok:
            first = next((l for l in (err or "").splitlines() if "rror" in l), "<no error line>")
            p["errors"][re.sub(r"'[^']*'", "'X'", re.sub(r"line \d+|: \d+:", "line N", first))[:80]] += 1
with_intake = {n: p for n, p in per.items() if p["intake"]}
no_intake_ok = {n: p for n, p in with_intake.items() if not p["intake_ok"]}
never_ok = {n: p for n, p in with_intake.items() if not p["any_ok"]}
print("functions with intake drafts:", len(with_intake))
print("  no intake draft ever compiled:", len(no_intake_ok),
      "| of which a later candidate compiled:", sum(1 for p in no_intake_ok.values() if p["any_ok"]))
print("  NOTHING ever compiled for the function:", len(never_ok))
errs = collections.Counter()
for p in never_ok.values():
    errs[p["errors"].most_common(1)[0][0] if p["errors"] else "<none>"] += 1
print("\ndominant first error of functions where nothing ever compiled:")
for e, n in errs.most_common(12):
    print(f"  {n:4d}  {e}")
