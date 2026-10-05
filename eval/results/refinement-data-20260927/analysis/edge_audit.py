import sqlite3, json, collections
db=sqlite3.connect("file:campaign.sqlite?mode=ro",uri=True)
rows=db.execute("""select c.strategy, coalesce(c.model,''), p.score, c.score, c.exact, c.func_addr,
  p.created_at, c.created_at, length(p.diff_summary), length(p.source_code), length(c.source_code), p.exact
from attempt_edges e join attempts p on p.id=e.parent_attempt_id join attempts c on c.id=e.child_attempt_id
where p.compiled=1 and c.compiled=1 and c.score>p.score and p.source_code is not null and c.source_code is not null""").fetchall()
exactf={r[0] for r in db.execute("select distinct func_addr from attempts where exact=1")}
print("improving compiled edges", len(rows))
sm=collections.Counter((r[0].split(':')[0].split('@')[0], r[1]) for r in rows)
print("strategy x model (top 30):")
for k,v in sm.most_common(30): print("  ",v,k)
d=[r[3]-r[2] for r in rows]
b=collections.Counter('<0.5' if x<0.5 else '<1' if x<1 else '<5' if x<5 else '>=5' for x in d)
print("delta buckets", b)
print("  exact children with delta<0.5:", sum(1 for r in rows if r[3]-r[2]<0.5 and r[4]))
print("edges in finished functions", sum(1 for r in rows if r[5] in exactf), "of which exact child", sum(1 for r in rows if r[4]))
print("parent already exact", sum(1 for r in rows if r[11]))
import datetime
def ts(x):
  try: return float(x)
  except: 
    try: return datetime.datetime.fromisoformat(str(x)).timestamp()
    except: return None
gaps=[(ts(r[7]) or 0)-(ts(r[6]) or 0) for r in rows]
g=collections.Counter('<1h' if x<3600 else '<1d' if x<86400 else '<7d' if x<7*86400 else '>=7d' for x in gaps)
print("parent->child creation gap", g, "sample created", rows[0][6], rows[0][7])
print("parent diff empty", sum(1 for r in rows if not r[8]))
print("source len quantiles parent", sorted(r[9] for r in rows)[len(rows)//2], sorted(r[9] for r in rows)[int(len(rows)*.95)])
