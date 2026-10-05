"""PRE-REGISTRATION + basin-escape measurement.  Written 2026-09-16 ~16:50 CDT, before results.

Question. Does a score-lowering edit that CHANGES THE FAULT CLASS of the residual (a candidate
signature of leaving a local basin) escape more often than one that lowers the score while the
fault class stays the same?

Pre-registered decision rule (fixed before running):
  H1 (basin signal): among explored setbacks, P(escape | changed-class) - P(escape | same-class)
      >= 0.05  AND  two-proportion z-test p < 0.05.
  Otherwise: NULL -> delayed-credit search is not supported by this data, publish the null.
  Escape = some strict descendant of the child scores strictly higher than the parent.
  Setback = compiled parent->child edge with child.score < parent.score, both ends compiled,
            both with a diff, and parent.score < 100 (else there is no headroom to escape into).

Secondary, also pre-registered:
  - Replicate the previously published aggregate (6.7% of explored setbacks later beat parent vs
    6.0% of all edges improving) so this run is comparable to what it replaces.
  - P(explored | changed) vs P(explored | same): if the pipeline already prefers changed-class
    setbacks, that is itself a signal, and it makes the conditional test a selected sample.
  - Strict (>parent) and material (>parent + 1.0) escape thresholds, both reported.
"""
import sqlite3
import math
import sys
from collections import Counter, defaultdict

sys.path.insert(0, '/mnt/c/Code/gameDecomp')
from solver import signals

KB = 'file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro'
ORDER = ('structural', 'regalloc', 'ordering', 'immediate', 'reloc', 'layout')


def dominant(profile) -> str | None:
    """The fault class with the most members; None when the residual is empty."""
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
    pval = math.erfc(abs(z) / math.sqrt(2))
    return z, pval


c = sqlite3.connect(KB, uri=True)
rows = c.execute("""select id, func_addr, score, compiled, exact, diff_summary, parent_attempt_id
                    from attempts where compiled=1 and diff_summary is not null and diff_summary != ''
                    and score is not null""").fetchall()

info = {}
children = defaultdict(list)
for aid, addr, score, compiled, exact, diff, parent in rows:
    prof = signals.analyse(diff, score, bool(exact), True)
    info[aid] = {'addr': addr, 'score': score, 'exact': exact, 'dom': dominant(prof),
                 'profile': {k: getattr(prof, k) for k in ORDER}}
    if parent is not None:
        children[parent].append(aid)

compiled_ids = {aid for aid, *_ in rows}

# desc_max[a] = best score anywhere strictly below a in the attempt tree.
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
    if parent is None or parent not in compiled_ids:
        continue
    p = info[parent]
    if p['score'] >= 100.0:
        continue                       # no headroom
    if score >= p['score']:
        continue                       # not a setback
    edges.append({
        'parent': parent, 'child': aid, 'p_score': p['score'], 'c_score': score,
        'p_dom': p['dom'], 'c_dom': info[aid]['dom'],
        'changed': p['dom'] != info[aid]['dom'],
        'explored': bool(children.get(aid)),
        'desc_best': subtree_best(aid),
    })

all_edges = [(aid, parent, score, info[parent]['score']) for aid, addr, score, compiled, exact, diff, parent in rows
             if parent in compiled_ids and info[parent]['score'] < 100.0]

print('scanned attempts (compiled, with diff):', len(rows))
print('eligible parent->child edges:', len(all_edges))
improving = sum(1 for _, _, s, ps in all_edges if s > ps)
print('  improving edges: %d (%.2f%%)' % (improving, 100.0 * improving / max(1, len(all_edges))))
print('score-lowering (setback) edges:', len(edges))

explored = [e for e in edges if e['explored']]
print('  explored (child was built on):', len(explored))
print()
print('=== distribution of fault-class transitions on setbacks ===')
tbl = Counter((e['p_dom'], e['c_dom']) for e in edges)
for (a, b), n in tbl.most_common(14):
    print(f'  {str(a):>10} -> {str(b):<10} {n}')
print()
print('=== exploration rate: is the pipeline already biased toward changed-class? ===')
ch = [e for e in edges if e['changed']]
sa = [e for e in edges if not e['changed']]
for label, grp in (('changed-class', ch), ('same-class', sa)):
    ex = sum(1 for e in grp if e['explored'])
    print(f'  {label:<14} n={len(grp):<5} explored={ex:<5} P(explored)={ex/max(1,len(grp)):.4f}')
z, p = two_prop_z(sum(1 for e in ch if e['explored']), len(ch),
                  sum(1 for e in sa if e['explored']), len(sa))
print(f'  z={z:.3f} p={p:.4f}')
print()
print('=== the previously published aggregate (replication) ===')
for label, grp in (('explored setbacks', explored),):
    esc = sum(1 for e in grp if e['desc_best'] is not None and e['desc_best'] > e['p_score'])
    print(f'  {label}: n={len(grp)} escaped={esc} rate={esc/max(1,len(grp)):.4f}')
print()
print('=== H1: escape rate, changed-class vs same-class (explored only) ===')


def report(grp, label, thr):
    if thr is None:
        hit = lambda e: e['desc_best'] is not None and e['desc_best'] > e['p_score']
    else:
        hit = lambda e: e['desc_best'] is not None and e['desc_best'] > e['p_score'] + thr
    a = [e for e in grp if e['changed']]
    b = [e for e in grp if not e['changed']]
    ka, kb = sum(hit(e) for e in a), sum(hit(e) for e in b)
    ra, rb = ka / max(1, len(a)), kb / max(1, len(b))
    z, p = two_prop_z(ka, len(a), kb, len(b))
    print(f'  [{label}]')
    print(f'    changed-class n={len(a):<5} escaped={ka:<4} rate={ra:.4f}')
    print(f'    same-class    n={len(b):<5} escaped={kb:<4} rate={rb:.4f}')
    print(f'    diff={ra-rb:+.4f}  z={z:.3f}  p={p:.4f}  -> '
          f'{"SIGNAL" if (ra-rb >= 0.05 and p < 0.05) else "null"}')
    return ra, rb


report(explored, 'strict > parent', None)
report(explored, 'material > parent + 1.0', 1.0)
print()
print('=== context: are changed-class setbacks simply WORSE (bigger score drop)? ===')
for label, grp in (('changed', ch), ('same', sa)):
    drops = [e['p_score'] - e['c_score'] for e in grp]
    if drops:
        drops.sort()
        print(f'  {label:<8} n={len(drops)} median drop={drops[len(drops)//2]:.2f}')
