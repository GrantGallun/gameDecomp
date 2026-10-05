import sqlite3
import re
import datetime
from collections import Counter, defaultdict

c = sqlite3.connect('file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro', uri=True)
attempted = {r[0] for r in c.execute('select distinct func_addr from attempts')}
compiled_any = {r[0] for r in c.execute('select distinct func_addr from attempts where compiled=1')}
never = attempted - compiled_any
names = {a: n for a, n in c.execute('select addr, name from functions')}
Q = ','.join(map(str, never))


def norm(err):
    if not err:
        return '(empty)'
    line = err.strip().splitlines()[0]
    line = re.sub(r'^[^:]*\.c[,:]?\s*', '', line)
    line = re.sub(r'\d+', 'N', line)
    return re.sub(r'\s+', ' ', line).strip()[:100]


print('=== attempt counts for the 199 never-compiled functions ===')
cnt = Counter()
rows = c.execute(f'select func_addr, count(*) from attempts where func_addr in ({Q}) group by 1').fetchall()
for _, n in rows:
    cnt['1' if n == 1 else '2-4' if n < 5 else '5-19' if n < 20 else '20+'] += 1
for k in ('1', '2-4', '5-19', '20+'):
    print(f'   {k:>5} attempts: {cnt[k]}')

print()
print('=== when were those attempts made? ===')
ts = [r[0] for r in c.execute(f'select created_at from attempts where func_addr in ({Q})')]
if ts:
    lo, hi = min(ts), max(ts)
    fmt = lambda t: datetime.datetime.fromtimestamp(t).strftime('%Y-%m-%d %H:%M')
    print('   earliest:', fmt(lo), ' latest:', fmt(hi))
    # how many attempts predate the most recent attempt of a function that DID compile?
    ok_ts = [r[0] for r in c.execute('select max(created_at) from attempts where compiled=1 group by func_addr')]
    med = sorted(ok_ts)[len(ok_ts)//2]
    old = sum(1 for t in ts if t < med)
    print(f'   median time of last successful compile: {fmt(med)}')
    print(f'   never-compiled attempts before that: {old}/{len(ts)} ({old/len(ts):.1%})')

print()
print('=== dominant error signature per never-compiled function ===')
dom = Counter()
fixed_by_dowhile = 0
for addr, in c.execute(f'select distinct func_addr from attempts where func_addr in ({Q})'):
    errs = [e for (e,) in c.execute(
        'select compiler_stderr from attempts where func_addr=? and compiled=0', (addr,))]
    if not errs:
        continue
    sigs = Counter(norm(e) for e in errs)
    top, n = sigs.most_common(1)[0]
    dom[top] += 1
    if 'do-while' in top:
        fixed_by_dowhile += 1
for k, v in dom.most_common(14):
    print(f'   {v:>4}  {k}')
print()
print(f'   functions whose dominant failure is the do-while ban: {fixed_by_dowhile}/{len(never)}')
print('   (a deterministic lowering for this already exists: rewrite_do_while)')

print()
print('=== do-while failures over time (is this stale?) ===')
buckets = Counter()
for addr, err, ts2 in c.execute('select func_addr, compiler_stderr, created_at from attempts where compiled=0'):
    if err and 'do-while' in err.splitlines()[0]:
        buckets[datetime.datetime.fromtimestamp(ts2).strftime('%Y-%m-%d')] += 1
for k in sorted(buckets):
    print(f'   {k}  {buckets[k]}')

print()
print('=== undefined-identifier tail: which symbols? ===')
und = Counter()
for (err,) in c.execute('select compiler_stderr from attempts where compiled=0 and compiler_stderr is not null'):
    m = re.search(r"'(\w+)' undefined", err)
    if m:
        und[m.group(1)] += 1
print('   distinct undefined symbols:', len(und), ' total occurrences:', sum(und.values()))
for k, v in und.most_common(12):
    print(f'   {v:>5}  {k}')

print()
print('=== no-text-symbols failures: which functions? ===')
nts = Counter()
for addr, err in c.execute("""select func_addr, compiler_stderr from attempts
                              where compiled=0 and compiler_stderr like '%no text symbols%'"""):
    nts[names.get(addr, str(addr))] += 1
print('   distinct functions affected:', len(nts))
for k, v in nts.most_common(10):
    print(f'   {v:>5}  {k}')
