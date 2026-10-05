"""Which strategies carry a model label, and do they have model OUTPUT (raw_response / proposals)?"""
import sqlite3, sys
db = sqlite3.connect(f"file:{sys.argv[1] if len(sys.argv) > 1 else 'campaign.sqlite'}?mode=ro", uri=True)
props = {r[0] for r in db.execute("select distinct child_attempt_id from model_proposals where child_attempt_id is not null")}
rows = db.execute("""select id, strategy, length(coalesce(raw_response,'')), coalesce(exact,0)
                     from attempts where coalesce(model,'') not in ('','zero-model','deterministic','none','m2c')""").fetchall()
import collections
agg = collections.defaultdict(lambda: [0, 0, 0, 0])
for i, s, raw, ex in rows:
    k = (s or '').split(':')[0].split('@')[0]
    a = agg[k]; a[0] += 1; a[1] += raw > 0; a[2] += i in props; a[3] += ex
print(f"{'strategy':45s} {'rows':>7s} {'raw_resp':>8s} {'proposal':>8s} {'exact':>6s}")
for k, (n, r, p, e) in sorted(agg.items(), key=lambda kv: -kv[1][0])[:25]:
    print(f"{k:45s} {n:7d} {r:8d} {p:8d} {e:6d}")
