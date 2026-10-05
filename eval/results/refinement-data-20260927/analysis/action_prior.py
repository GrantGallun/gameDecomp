import sqlite3, collections, hashlib, re, json, sys
sys.path.insert(0,'/mnt/c/Code/gameDecomp')
from eval.repair_dataset import split_for, sealed_functions, DEFAULT_SEED
db=sqlite3.connect("file:campaign.sqlite?mode=ro",uri=True)
fn={r[0]:(r[1],r[2]) for r in db.execute("select f.addr,f.name,t.name from functions f left join tus t on t.id=f.tu_id")}
sealed=sealed_functions()
att={r[0]:r[1:] for r in db.execute("select id, score, coalesce(compiled,0), coalesce(exact,0), func_addr from attempts")}
kids=collections.defaultdict(list)
E=db.execute("""select e.parent_attempt_id, e.child_attempt_id, e.relation, e.action, c.strategy, coalesce(c.model,'') from attempt_edges e join attempts c on c.id=e.child_attempt_id""").fetchall()
for p,c,*_ in E: kids[p].append(c)
memo={}
def desc(n,depth=0):
    if n in memo: return memo[n]
    memo[n]=False
    memo[n]=any(att.get(k,(0,0,0,0))[2] or desc(k) for k in kids.get(n,()))
    return memo[n]
sys.setrecursionlimit(1000000)
def kind(fam, action):
    a=(action or '').strip()
    a=re.sub(r"\b(of|in|for|to|symbol)\s+[A-Za-z_]\w*.*$", r"\1 X", a)
    a=re.sub(r"0x[0-9a-fA-F]+|\d+", "N", a)
    a=re.sub(r"\s+round N$", "", a)
    return fam+"|"+a[:60]
fam=lambda s:(s or '').split(':')[0].split('@')[0]
rows=[]
for p,c,rel,action,strat,model in E:
    if model not in ('','zero-model') or p not in att or c not in att or not att[p][1]: continue
    a,b=att[p],att[c]
    name,tu=fn.get(b[3],(None,None))
    if name in sealed: continue
    o='nc' if not b[1] else 'exact' if b[2] else 'up' if b[0]>a[0] else 'flat' if b[0]==a[0] else 'down'
    useful = o in ('exact','up') or (o=='flat' and desc(c))
    rows.append((split_for(tu,name,DEFAULT_SEED), kind(fam(strat),action), o, useful, bool(b[2]) or desc(c)))
print("rows", len(rows), collections.Counter(r[0] for r in rows))
print("distinct kinds", len({r[1] for r in rows}))
tab=collections.defaultdict(lambda:[0,0])
for s,k,o,u,x in rows:
    if s=='train': tab[k][0]+=1; tab[k][1]+=u
test=[r for r in rows if r[0]=='test']
tot=len(test); U=sum(r[3] for r in test); X=sum(r[4] for r in test)
print(f"test compiles {tot} useful {U} exact-reaching {X}")
for thr in (0.0, 0.002, 0.005, 0.01, 0.02, 0.05):
    for minn in (20,):
        skip=lambda k: tab[k][0]>=minn and tab[k][1]/tab[k][0]<=thr
        sk=[r for r in test if skip(r[1])]
        print(f"skip kinds with train useful-rate<={thr:<5} (n>={minn}): saves {len(sk)} compiles ({len(sk)/tot:.1%}), loses useful {sum(r[3] for r in sk)} / {U}, loses exact-reaching {sum(r[4] for r in sk)} / {X}")
print("unseen kinds in test", sum(1 for r in test if tab[r[1]][0]==0))
worst=sorted(((v[0],v[1],k) for k,v in tab.items() if v[0]>=200), key=lambda t:t[1]/t[0])[:15]
for n,u,k in worst: print(f"  {n:6d} useful {u:4d}  {k}")
