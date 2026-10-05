"""Why is each unmatched function unmatched: information, search, or representation?

PRE-REGISTRATION (written 2026-09-16 ~17:10 CDT, before running).

The question, stated so it is falsifiable. A diagnostic that labels functions is decoration
unless its labels predict something. So this is built as a BACKTEST: classify each function
using only attempts strictly before a cutoff T, then measure whether attempts after T improved
it. Labels that do not separate forward improvement are worthless and the script must say so.

Classes, taken from solver/signals.py's own taxonomy rather than invented here:

  REPAIR_PENDING   `repairable` (layout + immediate + ordering) dominates.
                   A pass exists and owns the fault: the fix is expressible and mechanical.
                   Failure mode is that the pass has not been run or has not converged -> SEARCH.
  INFORMATION      `conditional_repair` (reloc) dominates. Symbol/relocation identity is wrong,
                   which is a statement about a name or declaration the solver does not have.
                   The KB cannot repair it by searching C shapes -> INFORMATION.
  REPRESENTATION   `no_repair_implemented` (regalloc) + `unrepairable` (structural) dominate.
                   Nothing in the codebase repairs these; signals.py says so in the property
                   names. If search has also stalled, more sampling cannot help -> REPRESENTATION.
  MIXED            no single group reaches the dominance threshold.

Pre-registered hypotheses:
  H1  Forward-improvement rate differs across classes by >= 10 points (max class vs min class)
      with a two-proportion z-test p < 0.05.
  H1a Directional: REPAIR_PENDING and INFORMATION improve forward at a higher rate than
      REPRESENTATION. If H1 holds but the direction is reversed, that is a disconfirmation and
      must be reported as one.
  H0  No separation -> the labels are decoration, say so plainly.

Pre-registered guards:
  - A class that fires on zero functions is a FINDING, not a shrug (the fifth rule).
  - Functions with too little history before T are excluded and the exclusion is counted.
  - A function already exact before T is excluded (no headroom).
  - Deterministic and LLM-free, per the miner invariant.

REVISION 1 (2026-09-16, after run 1 produced a bogus result). Run 1 did not filter
`compiled=1`, so attempts that never compiled entered as score 0.0 and were classified from an
empty diff -- producing a spurious `CLEAN` class of 156 functions with 100 points of headroom.
Run 1 also read `functions.state` to find unmatched functions; that column is `'matched'` for
ALL 2113 rows and carries no information. Run 1's numbers are void. Corrected here:
  - history is compiled-only (a residual cannot be classified from a build that failed);
  - "unmatched" is derived from `attempts` (no compiled attempt has ever scored 100), not from
    `functions.state`;
  - the never-compiled population is reported separately, because "the model cannot write C that
    builds" is a different failure from any class below, and belongs to the admission gate.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solver import signals

DB = 'file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro'
DOMINANCE = 0.5          # a group must hold >50% of the residual to name the class
MIN_HISTORY = 3          # compiled attempts required before T for a classification to count

GROUPS = {
    'REPAIR_PENDING': ('repairable',),
    'INFORMATION': ('conditional_repair',),
    'REPRESENTATION': ('no_repair_implemented', 'unrepairable'),
}


def profile_of(diff: str, score: float, exact: bool) -> signals.Signals:
    return signals.analyse(diff or '', score, bool(exact), True)


def classify(prof: signals.Signals) -> tuple[str, dict]:
    """Name the class of a residual, and return the shares behind the name."""
    raw = {
        'repairable': prof.repairable,
        'conditional_repair': prof.conditional_repair,
        'no_repair_implemented': prof.no_repair_implemented,
        'unrepairable': prof.unrepairable,
    }
    total = sum(raw.values())
    shares = {k: (v / total if total else 0.0) for k, v in raw.items()}
    grouped = {name: sum(shares[k] for k in keys) for name, keys in GROUPS.items()}
    if total == 0:
        return 'CLEAN', {'shares': shares, 'grouped': grouped}
    best = max(grouped.values())
    if best <= DOMINANCE:
        return 'MIXED', {'shares': shares, 'grouped': grouped}
    return max(grouped, key=lambda k: grouped[k]), {'shares': shares, 'grouped': grouped}


def two_prop_z(k1, n1, k2, n2):
    if not n1 or not n2:
        return float('nan'), float('nan')
    p1, p2 = k1 / n1, k2 / n2
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return float('nan'), float('nan')
    z = (p1 - p2) / se
    return z, math.erfc(abs(z) / math.sqrt(2))


def load():
    c = sqlite3.connect(DB, uri=True)
    names = {addr: name for addr, name in c.execute('select addr, name from functions')}
    # Compiled only: a residual cannot be classified from a build that failed.
    rows = c.execute("""select id, func_addr, score, exact, diff_summary, created_at
                        from attempts
                        where compiled=1 and score is not null
                        order by created_at""").fetchall()
    # Every function that has ever had an attempt, compiled or not.
    attempted = {r[0] for r in c.execute('select distinct func_addr from attempts')}
    return names, rows, attempted


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cutoff-quantile', type=float, default=0.70,
                    help='fraction of attempt history used for classification')
    ap.add_argument('--min-gain', type=float, default=1.0,
                    help='forward improvement must exceed the pre-T best by this much')
    ap.add_argument('--json', type=Path, default=None)
    args = ap.parse_args()

    names, rows, attempted = load()
    times = sorted(r[5] for r in rows if r[5])
    cutoff = times[int(len(times) * args.cutoff_quantile)]
    print(f'compiled attempts: {len(rows)}  functions ever attempted: {len(attempted)}')
    print(f'cutoff T = {cutoff} ({int(args.cutoff_quantile*100)}th pct of compiled-attempt time)')
    print()

    before, after = defaultdict(list), defaultdict(list)
    for aid, addr, score, exact, diff, ts in rows:
        (before if (ts or 0) < cutoff else after)[addr].append((aid, score, exact, diff))

    classified, thin, exact_before = [], 0, 0
    for addr, hist in before.items():
        if len(hist) < MIN_HISTORY:
            thin += 1
            continue
        best = max(hist, key=lambda h: h[1])
        if best[1] >= 100.0 or best[2]:
            exact_before += 1
            continue
        cls, detail = classify(profile_of(best[3], best[1], bool(best[2])))
        fut = after.get(addr, [])
        forward = max((h[1] for h in fut), default=None)
        classified.append({
            'addr': addr, 'name': names.get(addr, '?'), 'class': cls,
            'best_before': best[1], 'n_before': len(hist), 'n_after': len(fut),
            'forward_best': forward,
            'improved': forward is not None and forward > best[1] + args.min_gain,
        })

    print(f'classifiable: {len(classified)}  '
          f'(excluded: {thin} too little pre-T history, {exact_before} already exact)')
    print()
    print('=== H1: does the label predict forward improvement? ===')
    by_class = defaultdict(list)
    for r in classified:
        by_class[r['class']].append(r)
    rates = {}
    print(f'  {"class":<16} {"n":>4} {"improved":>9} {"rate":>8}   {"med headroom":>13}')
    for cls in sorted(by_class, key=lambda k: -len(by_class[k])):
        grp = by_class[cls]
        k = sum(1 for r in grp if r['improved'])
        rates[cls] = (k, len(grp), k / len(grp))
        heads = sorted(100.0 - r['best_before'] for r in grp)
        print(f'  {cls:<16} {len(grp):>4} {k:>9} {k/len(grp):>8.4f}   '
              f'{heads[len(heads)//2]:>13.2f}')
    print()
    keys = list(rates)
    if len(keys) >= 2:
        hi = max(keys, key=lambda k: rates[k][2])
        lo = min(keys, key=lambda k: rates[k][2])
        z, p = two_prop_z(rates[hi][0], rates[hi][1], rates[lo][0], rates[lo][1])
        diff = rates[hi][2] - rates[lo][2]
        print(f'  max-min: {hi} {rates[hi][2]:.4f} vs {lo} {rates[lo][2]:.4f} '
              f'diff={diff:+.4f} z={z:.3f} p={p:.4f}')
        print(f'  H1 (>=10pts and p<0.05): '
              f'{"SIGNAL" if (diff >= 0.10 and p < 0.05) else "NULL - labels do not separate"}')
        if diff >= 0.10 and p < 0.05:
            ok = hi in ('REPAIR_PENDING', 'INFORMATION')
            print(f'  H1a direction: {"as predicted" if ok else "REVERSED - disconfirmed"}')
    for name in GROUPS:
        if rates.get(name, (0, 0, 0))[1] == 0:
            print(f'  FINDING: class {name} fired on zero functions in the backtest window.')

    print()
    print('=== the live population: no compiled attempt has ever reached 100 ===')
    best_all = {}
    exact_all = set()
    for aid, addr, score, exact, diff, ts in rows:
        if addr not in best_all or score > best_all[addr][1]:
            best_all[addr] = (aid, score, bool(exact), diff)
        if exact:
            exact_all.add(addr)
    never_compiled = attempted - set(best_all)
    live = {a: v for a, v in best_all.items() if a not in exact_all}
    print(f'  exact (score 100 achieved): {len(exact_all)}')
    print(f'  live (compiled, never exact): {len(live)}')
    print(f'  never compiled a single attempt: {len(never_compiled)}   <- admission-gate failure')
    print()
    live_cls, live_hi = Counter(), Counter()
    for addr, (aid, score, exact, diff) in live.items():
        cls, _ = classify(profile_of(diff, score, exact))
        live_cls[cls] += 1
        if score >= 90.0:
            live_hi[cls] += 1
    total_live = sum(live_cls.values())
    print(f'  {"class":<18} {"all":>6} {"share":>8}   {">=90% score":>12}')
    for cls, n in live_cls.most_common():
        print(f'  {cls:<18} {n:>6} {n/max(1,total_live):>8.2%}   {live_hi.get(cls,0):>12}')

    if args.json:
        args.json.write_text(json.dumps({
            'cutoff': cutoff, 'classifiable': len(classified),
            'rates': {k: {'n': v[1], 'improved': v[0], 'rate': v[2]} for k, v in rates.items()},
            'live': dict(live_cls), 'live_90': dict(live_hi),
            'exact': len(exact_all), 'live_total': len(live),
            'never_compiled': len(never_compiled),
            'excluded_thin': thin, 'excluded_exact': exact_before,
        }, indent=2))


if __name__ == '__main__':
    main()
