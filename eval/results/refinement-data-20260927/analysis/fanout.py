import sqlite3, collections, statistics as st
db=sqlite3.connect("file:campaign.sqlite?mode=ro",uri=True)
rows=db.execute("""select e.parent_attempt_id, e.child_attempt_id, e.relation, e.action, c.strategy, coalesce(c.model,''),
  p.score, c.score, coalesce(c.compiled,0), coalesce(c.exact,0), coalesce(p.compiled,0)
from attempt_edges e join attempts p on p.id=e.parent_attempt_id join attempts c on c.id=e.child_attempt_id""").fetchall()
print("all edges", len(rows))
fam=lambda s:(s or '').split(':')[0].split('@')[0]
det=[r for r in rows if r[5] in ('','zero-model') and r[10]==1]
print("deterministic edges w/ compiled parent", len(det))
print("relations", collections.Counter(r[2] for r in det).most_common(12))
out=collections.Counter()
for r in det:
    out['notcompiled' if not r[8] else 'exact' if r[9] else 'up' if r[7]>r[6] else 'flat' if r[7]==r[6] else 'down']+=1
print("outcomes", out)
# action vocabulary
acts=collections.Counter((fam(r[4]), (r[3] or '').split('(')[0][:40]) for r in det)
print("distinct (family,action)", len(acts)); 
for k,v in acts.most_common(25): print("  ",v,k)
# per parent: fanout and rank of first improving child in tried (child id) order
byp=collections.defaultdict(list)
for r in det: byp[(r[0],fam(r[4]))].append(r)
fan=[];first=[];noimp=0
for k,ch in byp.items():
    ch.sort(key=lambda r:r[1]); fan.append(len(ch))
    idx=[i for i,r in enumerate(ch) if r[8] and r[7]>r[6]]
    if idx: first.append((idx[0]+1, len(ch)))
    else: noimp+=1
print("parents", len(byp), "fanout median/p90/max", st.median(fan), sorted(fan)[int(.9*len(fan))], max(fan))
print("parents with no improving child", noimp, "with", len(first))
multi=[(a,b) for a,b in first if b>=5]
print("for fanout>=5: n", len(multi), "median rank of first improvement", st.median(a for a,b in multi), "median fanout", st.median(b for a,b in multi),
      "mean rank/fanout", round(st.mean(a/b for a,b in multi),3))
print("compiles spent on parents with no improving child", sum(len(ch) for k,ch in byp.items() if not any(r[8] and r[7]>r[6] for r in ch)))
