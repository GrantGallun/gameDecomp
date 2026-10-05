"""Summarise results.jsonl against the pre-registered predictions in README.md."""
import collections
import json
import sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else '/home/grant/decomp/runs/site-edits-20260929/results.jsonl')
rows = {}
for line in path.read_text().splitlines():
    row = json.loads(line)
    rows[row['function']] = row          # last write wins if a function was re-run
rows = list(rows.values())
RECALL = ['updateEndingJamSlideLeftToMarker', 'updateEndingJamSlideLeftFromFarRight',
          'enqueueSoundEffect', 'waitForCourseGateTrigger']

exact = [r for r in rows if r.get('exact')]
handoff = [r for r in rows if r.get('handoff')]
handoff_exact = [r for r in handoff if r['handoff'].get('exact')]
errors = [r for r in rows if r.get('error')]
improved = [r for r in rows if not r.get('exact') and r.get('best_gradient') and r.get('baseline_gradient')
            and r['best_gradient'] < r['baseline_gradient']]
declines = collections.Counter()
winning = collections.Counter()
proposals = []
for r in rows:
    first = next((t for t in r.get('trail', []) if 'attribution' in t), None)
    if first is not None:
        proposals.append(first.get('proposals', 0))
        if first.get('declined'):
            declines[first['declined']] += 1
    if r.get('exact'):
        kinds = [t['kind'] for t in r['trail'] if t.get('edit')]
        winners = [t for t in r['trail'] if t.get('complete')]
        path_kinds = []
        # the accepted path: best child per level plus the completing edit
        for t in winners:
            path_kinds.append(t['kind'])
        winning.update(path_kinds or kinds[-1:])

print(f'frame {len(rows)}; errors {len(errors)}')
print(f'exact (site edits alone): {len(exact)}  -> {sorted(r["function"] for r in exact)}')
print(f'exact after register handoff: {len(handoff_exact)} of {len(handoff)} handed off '
      f'-> {sorted(r["function"] for r in handoff_exact)}')
print(f'non-exact but gradient improved: {len(improved)}')
credited = [r for r in handoff_exact if r.get('best_gradient') and r.get('baseline_gradient')
            and r['best_gradient'] < r['baseline_gradient']]
print(f'handoff exacts credited to site edits (gradient moved first): {len(credited)} '
      f'-> {sorted(r["function"] for r in credited)}')
print(f'SITE-EDIT TOTAL: {len(exact) + len(credited)}')
family = [r for r in rows if r['function'].startswith('spawnEndingCredits')]
print('spawnEndingCredits*: ' + ', '.join(f"{r['function']} {r.get('baseline_gradient')}->{r.get('best_gradient')}"
                                         f"{' EXACT' if r.get('exact') or (r.get('handoff') or {}).get('exact') else ''}"
                                         for r in family))
print(f'recall: {sum(1 for r in rows if r["function"] in RECALL and (r.get("exact") or (r.get("handoff") or {}).get("exact")))} of 4')
print(f'functions with zero proposals: {sum(1 for p in proposals if p == 0)} of {len(proposals)}; reasons {dict(declines)}')
print(f'completing edit kinds: {dict(winning)}')
compiles = [r.get('compiles', 0) for r in rows]
print(f'site-edit compiles: total {sum(compiles)}, mean {sum(compiles) / max(1, len(compiles)):.1f}; '
      f'handoff compiles {sum((r.get("handoff") or {}).get("compiles", 0) for r in rows)}')
for r in errors:
    print('ERROR', r['function'], r['error'][:200])
