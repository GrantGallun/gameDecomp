import sqlite3
import re
from collections import Counter, defaultdict

c = sqlite3.connect('file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro', uri=True)

print('=== distinct extract_status ===')
for row in c.execute("""select coalesce(extract_status,'(null)'), count(*) from attempts
                        group by 1 order by 2 desc"""):
    print('  ', row)

print()
print('=== distinct done_reason ===')
for row in c.execute("""select coalesce(done_reason,'(null)'), count(*) from attempts
                        group by 1 order by 2 desc limit 12"""):
    print('  ', row)

print()
print('=== model over time (attempt counts) ===')
for row in c.execute("""select coalesce(model,'(none)'), count(*), min(created_at), max(created_at)
                        from attempts group by 1 order by 2 desc limit 15"""):
    print('  ', row)

print()
print('=== strategy over time ===')
for row in c.execute("""select coalesce(strategy,'(none)'), count(*) from attempts
                        group by 1 order by 2 desc limit 15"""):
    print('  ', row)

print()
print('=== the never-compiled population ===')
attempted = {r[0] for r in c.execute('select distinct func_addr from attempts')}
compiled_any = {r[0] for r in c.execute('select distinct func_addr from attempts where compiled=1')}
never = attempted - compiled_any
print('  attempted:', len(attempted), ' ever compiled:', len(compiled_any), ' never:', len(never))
print()
print('  attempts belonging to never-compiled functions:',
      c.execute('select count(*) from attempts where func_addr in (%s)'
                % ','.join(map(str, never))).fetchone()[0])
print('  ... of which extracted C:', c.execute(
    'select count(*) from attempts where func_addr in (%s) and extract_status="ok"'
    % ','.join(map(str, never))).fetchone()[0])

print()
print('=== compile-error signatures across ALL failed attempts ===')


def norm(err):
    if not err:
        return '(empty)'
    line = err.strip().splitlines()[0]
    line = re.sub(r'^[^:]*\.c[,:]?\s*', '', line)
    line = re.sub(r'\d+', 'N', line)
    line = re.sub(r'\s+', ' ', line).strip()
    return line[:110]


sig_all = Counter()
sig_never = Counter()
never_set = never
for addr, err in c.execute('select func_addr, compiler_stderr from attempts where compiled=0'):
    sig_all[norm(err)] += 1
    if addr in never_set:
        sig_never[norm(err)] += 1
print('  --- all failed attempts, top 18 ---')
for k, v in sig_all.most_common(18):
    print(f'    {v:>6}  {k}')
print()
print('  --- failed attempts inside never-compiled functions, top 12 ---')
for k, v in sig_never.most_common(12):
    print(f'    {v:>6}  {k}')

print()
print('=== refusal / empty-response detection, over ALL attempts ===')
n_ref = 0
for (raw,) in c.execute('select raw_response from attempts where raw_response is not null and raw_response != ""'):
    low = (raw or '').strip().lower()
    if len(low) < 200 and ("i'm sorry" in low or 'i cannot' in low or "i can't" in low
                           or 'unable to' in low):
        n_ref += 1
print('  short non-answers (<200 chars, apology-shaped):', n_ref)

print()
print('=== response length distribution (extracted C), failed vs compiled ===')
buckets = defaultdict(Counter)
for addr, compiled, src in c.execute('select func_addr, compiled, source_code from attempts'):
    ln = len(src or '')
    b = ('0-100' if ln < 100 else '100-500' if ln < 500 else '500-2k' if ln < 2000
         else '2k-10k' if ln < 10000 else '10k+')
    buckets['compiled' if compiled else 'failed'][b] += 1
for k in ('failed', 'compiled'):
    print(' ', k, dict(buckets[k]))
