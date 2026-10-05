import sqlite3
import datetime
from collections import Counter, defaultdict

c = sqlite3.connect('file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro', uri=True)

print('=== compile rate by day (all attempts) ===')
byday = defaultdict(lambda: [0, 0])
for ts, compiled in c.execute('select created_at, compiled from attempts'):
    d = datetime.datetime.fromtimestamp(ts).strftime('%m-%d')
    byday[d][1] += 1
    if compiled:
        byday[d][0] += 1
for d in sorted(byday):
    k, n = byday[d]
    bar = '#' * int(40 * k / n) if n else ''
    print(f'   {d}  {k:>5}/{n:<5} {k/n:>6.1%} {bar}')

print()
print('=== the 199: has ANY of them been attempted since 2026-09-07? ===')
attempted = {r[0] for r in c.execute('select distinct func_addr from attempts')}
compiled_any = {r[0] for r in c.execute('select distinct func_addr from attempts where compiled=1')}
never = attempted - compiled_any
cut = datetime.datetime(2026, 9, 7).timestamp()
recent = 0
for (addr,) in c.execute('select distinct func_addr from attempts where created_at >= ?', (cut,)):
    if addr in never:
        recent += 1
print(f'   {recent} of {len(never)} attempted since 2026-09-07')

print()
print('=== per-function outcome for the 199, by dominant failure ===')
names = {a: n for a, n in c.execute('select addr, name from functions')}
import re
def norm(err):
    if not err:
        return '(empty)'
    line = err.strip().splitlines()[0]
    line = re.sub(r'^[^:]*\.c[,:]?\s*', '', line)
    line = re.sub(r'\d+', 'N', line)
    return re.sub(r'\s+', ' ', line).strip()[:60]

groups = defaultdict(list)
for addr, in c.execute('select distinct func_addr from attempts'):
    if addr not in never:
        continue
    errs = [e for (e,) in c.execute('select compiler_stderr from attempts where func_addr=? and compiled=0', (addr,))]
    n_att = c.execute('select count(*) from attempts where func_addr=?', (addr,)).fetchone()[0]
    top = Counter(norm(e) for e in errs).most_common(1)[0][0] if errs else '(none)'
    groups[top].append((addr, n_att))

print(f'   {"dominant failure":<52} {"funcs":>5} {"attempts":>8}')
for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:12]:
    print(f'   {k:<52} {len(v):>5} {sum(a for _, a in v):>8}')

print()
print('=== are the fixes newer than the failures? ===')
import os, time
for f in ('solver/compile_chain.py', 'tools/score_repo_function.py',
          'solver/llm.py', 'eval/trajectory_factory.py'):
    p = '/mnt/c/Code/gameDecomp/' + f
    if os.path.exists(p):
        print(f'   {f:<40} mtime {datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M")}')
