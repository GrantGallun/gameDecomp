import sqlite3, collections
db=sqlite3.connect("file:campaign.sqlite?mode=ro",uri=True)
att={r[0]:r[1:] for r in db.execute("select id, score, coalesce(compiled,0), coalesce(exact,0), coalesce(model,''), strategy from attempts")}
par=collections.defaultdict(list); kids=collections.defaultdict(list)
for p,c in db.execute("select parent_attempt_id, child_attempt_id from attempt_edges"):
    par[c].append(p); kids[p].append(c)
exacts=[i for i,a in att.items() if a[2]]
# for each exact with lineage, walk the single best-scoring parent chain back; record step signs
kinds=collections.Counter(); depth=[]
for e in exacts:
    if e not in par: kinds['no-lineage']+=1; continue
    cur=e; drops=0; flats=0; steps=0; seen=set()
    while cur in par and cur not in seen:
        seen.add(cur)
        ps=[p for p in par[cur] if p in att]
        if not ps: break
        p=max(ps,key=lambda x:(att[x][0] or -1))
        a,b=att[p],att[cur]
        if a[1] and b[1]:
            if b[0]<a[0]: drops+=1
            elif b[0]==a[0]: flats+=1
        steps+=1; cur=p
    depth.append(steps)
    kinds['path-with-drop' if drops else 'path-with-flat-only' if flats else 'monotone-up']+=1
print("exact attempts", len(exacts), kinds, "median depth", sorted(depth)[len(depth)//2] if depth else None)
# descendant value: does a child (one-step outcome) have an exact descendant?
memo={}
import sys; sys.setrecursionlimit(100000)
def has_exact_desc(n):
    if n in memo: return memo[n]
    memo[n]=False
    r=any(att.get(k,(0,0,0))[2] or has_exact_desc(k) for k in kids.get(n,()))
    memo[n]=r; return r
out=collections.Counter()
for p,cs in list(kids.items()):
    if p not in att or not att[p][1]: continue
    for c in cs:
        if c not in att: continue
        a,b=att[p],att[c]
        if not b[1]: o='notcompiled'
        elif b[2]: o='exact'
        elif b[0]>a[0]: o='up'
        elif b[0]==a[0]: o='flat'
        else: o='down'
        out[(o, bool(b[2]) or has_exact_desc(c))]+=1
for o in ('up','flat','down','notcompiled'):
    t=out[(o,True)]+out[(o,False)]
    print(o, t, "with exact descendant", out[(o,True)], round(out[(o,True)]/max(t,1)*100,2),"%")
