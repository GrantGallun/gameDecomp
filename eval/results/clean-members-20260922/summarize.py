"""Summarize the completed, source-bound replay and render its failure histogram."""
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval.quality_ratchet import compare

OUT = Path(__file__).resolve().parent
paired = json.loads((OUT / 'paired-final.json').read_text())
assert len(paired['rows']) == paired['expected'] == 200
receipts = {}
summary = {'states': 200, 'compiler_attempts': paired['calls'], 'arms': {}}
for arm in ('before', 'after'):
    rows = [r[arm] for r in paired['rows']]
    classes = Counter(k for row in rows for k in row['classes'])
    summary['arms'][arm] = {
        'ido_compiled': sum(r['compiled'] for r in rows),
        'frontend_passed': sum(r['frontend'] == 'passed' for r in rows),
        'ido_and_frontend': sum(r['compiled'] and r['frontend'] == 'passed' for r in rows),
        'object_exact': sum(r['exact'] for r in rows),
        'frontend_errors': sum(r['errors'] for r in rows),
        'classes': dict(classes.most_common()),
    }
    receipts[arm] = {'rows': [dict(function=r['function'], draft_sha256=r[arm]['source_sha256'],
        sequence=dict(compiled=r[arm]['compiled'], exact=r[arm]['exact'],
                      frontend_passed=r[arm]['frontend'] == 'passed'),
        diagnostic_trace=[dict(after='final', errors=r[arm]['errors'])]) for r in paired['rows']]}
    (OUT / f'ratchet-{arm}.json').write_text(json.dumps(receipts[arm], indent=2) + '\n')
ratchet = compare(receipts['before'], receipts['after'])
assert ratchet['holds']
summary['quality_ratchet'] = {'holds': True, 'better': len(ratchet['better']), 'worse': len(ratchet['worse'])}
summary['new_exact'] = [dict(function=r['function'], already_exact_in_research_kb=r['already_exact_in_research_kb'])
                        for r in paired['rows'] if r['after']['exact'] and not r['before']['exact']]
summary['actions'] = dict(Counter(t['action'] for r in paired['rows'] for t in r['trace'] if t['changed']))
summary['changed_sources'] = sum(r['before']['source_sha256'] != r['after']['source_sha256'] for r in paired['rows'])
for r in paired['rows']:
    folder = OUT / 'states' / r['function']
    for arm, filename in (('before', 'before.c'), ('after', 'final.c')):
        assert hashlib.sha256((folder / filename).read_text().encode()).hexdigest() == r[arm]['source_sha256']
    assert r['before']['frontend'] != 'unavailable' and r['after']['frontend'] != 'unavailable'

member_bases = {}
for arm, filename in (('before', 'frontend.json'), ('after', 'final-frontend.json')):
    states = Counter()
    for r in paired['rows']:
        front = json.loads((OUT / 'states' / r['function'] / filename).read_text())
        bases = {m.group(1) for e in front['errors']
                 if (m := re.match(r"member reference base type '([^']+)'", e['what']))}
        states.update(bases)
    member_bases[arm] = dict(states.most_common())
summary['member_base_states'] = member_bases
(OUT / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
(OUT / 'ratchet.json').write_text(json.dumps(ratchet, indent=2) + '\n')

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
classes = summary['arms']['before']['classes']
labels = list(classes)
fig, ax = plt.subplots(figsize=(10, 6.3))
y = list(range(len(labels)))
before = [classes[k] for k in labels]
after = [summary['arms']['after']['classes'].get(k, 0) for k in labels]
ax.barh([v - .19 for v in y], before, height=.36, label='Before this round', color='#94a3b8')
bars = ax.barh([v + .19 for v in y], after, height=.36, label='After repairs', color='#167b85')
ax.bar_label(bars, padding=4, fontsize=9)
ax.set_yticks(y, [label.replace('-', ' ') for label in labels])
ax.invert_yaxis()
ax.set_xlim(0, 155)
ax.set_xlabel('Functions with this diagnostic class (same 200 candidates; classes overlap)')
ax.set_title('Clean intake failure histogram | September 22, 2026', loc='left', weight='bold', pad=14)
ax.legend(loc='lower right', frameon=False)
ax.spines[['top', 'right', 'left']].set_visible(False)
ax.grid(axis='x', alpha=.15)
ax.set_axisbelow(True)
fig.tight_layout()
fig.savefig(OUT / 'histogram.png', dpi=150)
plt.close(fig)
print(json.dumps(summary, indent=2))
