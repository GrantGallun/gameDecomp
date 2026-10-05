"""Cost per attempt (wall_ms) by generator, and who creates root candidates. Read-only."""
import collections
import sqlite3
import sys

db = sqlite3.connect(f"file:{sys.argv[1] if len(sys.argv) > 1 else 'campaign.sqlite'}?mode=ro",
                     uri=True)
authored = "length(coalesce(raw_response,''))>0 and coalesce(model,'') not in ('','zero-model')"
for label, where in (("model-authored", authored),
                     ("regalloc-search-probe", "strategy='regalloc-search-probe'"),
                     ("repair-d*", "strategy like 'repair-d%'"),
                     ("operand-repair", "strategy like 'operand-repair%'")):
    ms = sorted(r[0] for r in db.execute(f"select wall_ms from attempts where {where} and wall_ms>0"))
    if ms:
        print(f"{label:24s} n={len(ms):6d} median {ms[len(ms)//2]/1000:7.2f}s  "
              f"p90 {ms[int(len(ms)*.9)]/1000:7.2f}s")
children = {r[0] for r in db.execute("select distinct child_attempt_id from attempt_edges")}
roots = collections.Counter()
for i, strategy, model, raw in db.execute(
        "select id, strategy, coalesce(model,''), length(coalesce(raw_response,'')) from attempts"):
    if i in children:
        continue
    kind = "model-authored" if raw and model not in ("", "zero-model") else \
        (strategy or "").split(":")[0].split("@")[0]
    roots[kind] += 1
print("\nroot candidates (no parent edge) by creator:", sum(roots.values()))
for k, v in roots.most_common(14):
    print(f"  {v:7d}  {k}")
