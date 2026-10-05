"""Where do candidates die at the compiler, and are they ever rescued? Read-only, campaign DB."""
import collections
import re
import sqlite3
import sys

db = sqlite3.connect(f"file:{sys.argv[1] if len(sys.argv) > 1 else 'campaign.sqlite'}?mode=ro",
                     uri=True)
kids = collections.defaultdict(list)
for p, c in db.execute("select parent_attempt_id, child_attempt_id from attempt_edges"):
    kids[p].append(c)
compiled = dict(db.execute("select id, coalesce(compiled,0) from attempts"))
fam = collections.defaultdict(lambda: [0, 0, 0])
errors = collections.defaultdict(collections.Counter)
for aid, strategy, model, raw, ok, err in db.execute(
        "select id, strategy, coalesce(model,''), length(coalesce(raw_response,'')), "
        "coalesce(compiled,0), compiler_stderr from attempts"):
    author = "model" if raw and model not in ("", "zero-model") else "deterministic"
    family = f"{author}:{(strategy or '').split(':')[0].split('@')[0]}"
    f = fam[family]
    f[0] += 1
    if not ok:
        f[1] += 1
        f[2] += any(compiled.get(k) for k in kids.get(aid, ()))   # a compiling child exists
        first = next((l for l in (err or "").splitlines() if "rror" in l), "<no error line>")
        first = re.sub(r"'[^']*'", "'X'", re.sub(r"line \d+|: \d+:", "line N", first))[:90]
        errors[author][first] += 1
print(f"{'family':45s} {'attempts':>8s} {'failed':>7s} {'fail%':>6s} {'rescued':>8s}")
for family, (n, bad, rescued) in sorted(fam.items(), key=lambda kv: -kv[1][1])[:18]:
    print(f"{family[:45]:45s} {n:8d} {bad:7d} {bad / n:6.1%} {rescued:8d}")
for author in ("model", "deterministic"):
    print(f"\n{author}: top first errors")
    for e, n in errors[author].most_common(8):
        print(f"  {n:6d}  {e}")
