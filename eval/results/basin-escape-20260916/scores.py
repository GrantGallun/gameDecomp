import sqlite3
from collections import Counter

c = sqlite3.connect('file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro', uri=True)

print('=== score by exact (compiled=1) ===')
for row in c.execute("""select exact, count(*), round(min(score),3), round(avg(score),3), round(max(score),3)
                        from attempts where compiled=1 group by exact"""):
    print('  exact=%s n=%s min=%s avg=%s max=%s' % row)

print()
print('=== score by compiled ===')
for row in c.execute("""select compiled, count(*), round(min(score),3), round(avg(score),3), round(max(score),3)
                        from attempts group by compiled"""):
    print('  compiled=%s n=%s min=%s avg=%s max=%s' % row)

print()
print('=== functions.state vs best_score ===')
for row in c.execute("""select state, count(*), round(min(best_score),3), round(avg(best_score),3), round(max(best_score),3)
                        from functions group by state"""):
    print('  state=%s n=%s min=%s avg=%s max=%s' % row)

print()
print('=== a few matched functions ===')
for row in c.execute("""select name, state, best_score from functions where state='matched' limit 5"""):
    print('  ', row)

print()
print('=== score histogram (compiled=1) ===')
hist = Counter()
for (s,) in c.execute('select score from attempts where compiled=1 and score is not null'):
    hist[int(s // 10) * 10] += 1
for k in sorted(hist):
    print(f'  {k:>4}-{k+9:<4} {hist[k]}')

print()
print('=== parent linkage coverage ===')
print('  attempts with parent:', c.execute('select count(*) from attempts where parent_attempt_id is not null').fetchone()[0])
print('  of those compiled:', c.execute('select count(*) from attempts where parent_attempt_id is not null and compiled=1').fetchone()[0])
print('  attempts with diff_summary:', c.execute('select count(*) from attempts where diff_summary is not null and diff_summary != ""').fetchone()[0])
