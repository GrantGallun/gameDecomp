"""Can the search move the axis that is actually blocking a function?

Why this exists. `eval/residual_diagnosis.py` classified functions by which fault group dominates
and then failed its backtest: functions labelled REPRESENTATION improved forward at 0.310, no
worse than REPAIR_PENDING at 0.333. The reason is a category error in that classifier, and it is
worth stating plainly because it invalidates the earlier reading:

    signals.py's `no_repair_implemented` and `unrepairable` describe what the PASS LIBRARY owns.
    They say nothing about what the SEARCH can express. The model can write any C, so "no pass
    repairs this" is not "this is not representable."

So the ownership axis does not predict search progress, and the NULL is the correct result for
that question. The right question is narrower and measurable:

    For the fault class that dominates a function's residual, has ANY attempt ever produced a
    strictly lower count of that class than the function's own best attempt did?

  - YES -> the axis is reachable. The search can express movement on it, so the blocker is
           search effort or prior, not representation.
  - NO  -> across every compiled attempt ever made, nothing moved that axis below where the best
           attempt left it. With enough attempts, that is evidence the move set cannot express
           the fix: a representation failure.

PRE-REGISTRATION (before running):
  Population: functions with a compiled attempt that never reached 100 (the live set).
  Axis: the dominant fault class of the function's best-scoring attempt.
  Mobility: min(count of that class over all compiled attempts) < count at the best attempt.
  Stratify by attempt count, because "never moved it in 3 attempts" is not evidence of anything.
  Hypothesis: if representation failure is real and dominant, the share that never moved the axis
  should stay high as attempt count grows. If it falls toward zero, the axis is reachable and the
  blocker is search. No significance test is pre-registered; this is a descriptive map, and the
  honest output is the table.

REVISION 1 (after run 1, before publishing). The pre-registered mobility definition has a false
negative. `floor < count at the BEST attempt` reports "never moved" whenever the best-scoring
attempt happens to be the one that already minimised the axis -- but that is precisely the case
where the search DID move the axis, and moved it all the way down. Both situations produce
floor == c_best, and they mean opposite things.

Corrected primary definition: mobility = min(count over all attempts) < count at the FIRST
attempt -- did the search ever get below where it started on the blocking axis? Both definitions
are reported side by side so the reader can see how much the answer depends on the choice.
"""
from __future__ import annotations

import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solver import signals

DB = 'file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro'
AXES = ('layout', 'offset', 'width', 'structural', 'reloc', 'regalloc', 'ordering', 'immediate')


def profile_counts(diff: str, score: float, exact: bool) -> dict:
    s = signals.analyse(diff or '', score, bool(exact), True)
    return {a: getattr(s, a) for a in AXES}


def dominant(counts: dict) -> str | None:
    best = max(counts.values())
    if best == 0:
        return None
    for a in AXES:
        if counts[a] == best:
            return a
    return None


c = sqlite3.connect(DB, uri=True)
rows = c.execute("""select func_addr, score, exact, diff_summary, created_at
                    from attempts
                    where compiled=1 and score is not null and diff_summary is not null
                    order by func_addr, created_at""").fetchall()

per_func = defaultdict(list)
for addr, score, exact, diff, ts in rows:
    per_func[addr].append((score, bool(exact), diff))

live = {}
for addr, hist in per_func.items():
    if any(h[1] or h[0] >= 100.0 for h in hist):
        continue
    live[addr] = hist

print(f'live functions (compiled, never exact): {len(live)}')
print()
print('=== mobility of the dominant axis, by attempt count ===')
print('  "moved" = some attempt produced a strictly lower count of that class')
print('            than the function\'s own best attempt did.')
print()
bands = [(1, 3), (3, 6), (6, 15), (15, 10**9)]
print(f'  {"attempts":<12} {"funcs":>6} {"below first":>12} {"below best":>11} {"P(never)":>9}')
summary = {}
for lo, hi in bands:
    grp = {a: h for a, h in live.items() if lo <= len(h) < hi}
    below_first = below_best = never = 0
    for addr, hist in grp.items():
        hist = sorted(hist, key=lambda x: x[0])
        best = max(hist, key=lambda x: x[0])
        bet = dominant(profile_counts(best[2], best[0], best[1]))
        if bet is None:
            never += 1
            continue
        counts = [profile_counts(d, s, e)[bet] for s, e, d in hist]
        cb = profile_counts(best[2], best[0], best[1])[bet]
        floor = min(counts)
        if floor < counts[0]:
            below_first += 1
        if floor < cb:
            below_best += 1
        if floor >= counts[0]:
            never += 1
    n = len(grp)
    summary[(lo, hi)] = (n, below_first, below_best, never)
    label = f'[{lo},{hi if hi < 10**9 else "inf"})'
    print(f'  {label:<12} {n:>6} {below_first:>12} {below_best:>11} '
          f'{(never/n if n else 0):>9.3f}')

print()
print('=== who are the never-moved, high-attempt functions? (primary definition) ===')
hard = []
for addr, hist in live.items():
    if len(hist) < 6:
        continue
    hist = sorted(hist, key=lambda x: x[0])
    best = max(hist, key=lambda x: x[0])
    counts_best = profile_counts(best[2], best[0], best[1])
    axis = dominant(counts_best)
    if axis is None:
        continue
    counts = [profile_counts(d, s, e)[axis] for s, e, d in hist]
    if min(counts) >= counts[0]:
        hard.append((addr, len(hist), best[0], axis, counts[0], min(counts)))
hard.sort(key=lambda r: -r[1])
print(f'  {len(hard)} functions with >=6 attempts that NEVER got below their FIRST '
      f'attempt on the blocking axis')
print(f'  {"func":<12} {"attempts":>8} {"best":>7} {"axis":<12} {"first":>6} {"floor":>6}')
names = {a: n for a, n in c.execute('select addr, name from functions')}
for addr, n, sc, axis, cb, fl in hard[:20]:
    print(f'  {names.get(addr, str(addr))[:12]:<12} {n:>8} {sc:>7.2f} {axis:<12} {cb:>6} {fl:>6}')
if len(hard) > 20:
    print(f'  ... and {len(hard) - 20} more')

print()
print('=== axis distribution among never-moved ===')
print('  ', Counter(r[3] for r in hard).most_common())
print()
print('=== reference: axis distribution among ALL live functions ===')
allax = Counter()
for addr, hist in live.items():
    best = max(hist, key=lambda x: x[0])
    a = dominant(profile_counts(best[2], best[0], best[1]))
    allax[a] += 1
print('  ', allax.most_common())
