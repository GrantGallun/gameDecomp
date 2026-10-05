"""Control for the basin_escape pre-registered run. Do not edit that file.

Confound. Changed-class setbacks have median score drop 10.16 vs 0.87 for same-class, so "changed
class" is collinear with "bigger move". If magnitude alone explains the escape rate, the fault
profile adds nothing and the practical rule is the scalar "explore big drops" -- a much weaker and
already-known claim. This script stratifies by drop size to separate the two.

Also reports the selection problem: changed-class edges are explored at 2.1x the rate (p=0.002), so
the conditional test runs on a sample the pipeline already chose. Nothing here can fix that; only
randomised exploration can, and that is the recommendation if the effect survives.
"""
import sqlite3
import math
import sys
from collections import defaultdict

sys.path.insert(0, '/mnt/c/Code/gameDecomp')
from solver import signals

KB = 'file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro'
ORDER = ('structural', 'regalloc', 'ordering', 'immediate', 'reloc', 'layout')


def dominant(profile):
    counts = {k: getattr(profile, k) for k in ORDER}
    best = max(counts.values())
    if best == 0:
        return None
    for k in ORDER:
        if counts[k] == best:
            return k
    return None


def two_prop_z(k1, n1, k2, n2):
    if n1 == 0 or n2 == 0:
        return float('nan'), float('nan')
    p1, p2 = k1 / n1, k2 / n2
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return float('nan'), float('nan')
    z = (p1 - p2) / se
    return z, math.erfc(abs(z) / math.sqrt(2))


c = sqlite3.connect(KB, uri=True)
rows = c.execute("""select id, func_addr, score, compiled, exact, diff_summary, parent_attempt_id
                    from attempts where compiled=1 and diff_summary is not null and diff_summary != ''
                    and score is not null""").fetchall()
info, children = {}, defaultdict(list)
for aid, addr, score, compiled, exact, diff, parent in rows:
    prof = signals.analyse(diff, score, bool(exact), True)
    info[aid] = {'score': score, 'dom': dominant(prof)}
    if parent is not None:
        children[parent].append(aid)
ids = {a for a, *_ in rows}
desc_max = {}


def subtree_best(a):
    if a in desc_max:
        return desc_max[a]
    best = None
    for ch in children.get(a, ()):
        cand = info[ch]['score']
        deep = subtree_best(ch)
        if deep is not None and (cand is None or deep > cand):
            cand = deep
        if cand is not None and (best is None or cand > best):
            best = cand
    desc_max[a] = best
    return best


edges = []
for aid, addr, score, compiled, exact, diff, parent in rows:
    if parent is None or parent not in ids:
        continue
    ps = info[parent]['score']
    if ps >= 100.0 or score >= ps:
        continue
    edges.append({'child': aid, 'p_score': ps, 'c_score': score,
                  'drop': ps - score, 'changed': info[parent]['dom'] != info[aid]['dom'],
                  'explored': bool(children.get(aid)), 'desc_best': subtree_best(aid)})
explored = [e for e in edges if e['explored']]


def esc(grp):
    k = sum(1 for e in grp if e['desc_best'] is not None and e['desc_best'] > e['p_score'])
    return k, len(grp), (k / len(grp) if grp else float('nan'))


print('=== control: stratify by drop size (explored setbacks) ===')
print('   if class-change is the driver, the same-class column stays flat as drop grows;')
print('   if magnitude is the driver, it climbs to match the changed-class column.')
print()
bands = [(0, 1), (1, 3), (3, 10), (10, 100)]
print(f'  {"drop band":<12} {"changed k/n":>14} {"rate":>8}   {"same k/n":>14} {"rate":>8}')
for lo, hi in bands:
    ch = [e for e in explored if e['changed'] and lo <= e['drop'] < hi]
    sa = [e for e in explored if not e['changed'] and lo <= e['drop'] < hi]
    kc, nc, rc = esc(ch)
    ks, ns, rs = esc(sa)
    print(f'  [{lo:>2},{hi:>3})     {f"{kc}/{nc}":>14} {rc:>8.4f}   {f"{ks}/{ns}":>14} {rs:>8.4f}')

print()
print('=== the decisive cells ===')
big_same = [e for e in explored if not e['changed'] and e['drop'] >= 10]
big_ch = [e for e in explored if e['changed'] and e['drop'] >= 10]
small_ch = [e for e in explored if e['changed'] and e['drop'] < 10]
ks, ns, rs = esc(big_same)
kc, nc, rc = esc(big_ch)
k2, n2, r2 = esc(small_ch)
print(f'  same-class, drop >= 10 : {ks}/{ns} = {rs:.4f}   <- does magnitude alone escape?')
print(f'  changed,    drop >= 10 : {kc}/{nc} = {rc:.4f}')
print(f'  changed,    drop <  10 : {k2}/{n2} = {r2:.4f}   <- does change alone escape?')
if ns and nc:
    z, p = two_prop_z(kc, nc, ks, ns)
    print(f'  changed vs same, both drop>=10: diff={rc-rs:+.4f} z={z:.3f} p={p:.4f}')
print()
print('=== population rarity ===')
print(f'  setbacks total      : {len(edges)}')
print(f'  changed-class       : {sum(1 for e in edges if e["changed"])}'
      f'  ({100*sum(1 for e in edges if e["changed"])/len(edges):.2f}% of setbacks)')
print(f'  changed & explored  : {sum(1 for e in edges if e["changed"] and e["explored"])}')
