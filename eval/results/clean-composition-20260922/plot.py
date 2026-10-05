import json
from pathlib import Path
OUT = Path(__file__).resolve().parent
pre = json.loads((OUT / 'inventory-before-reviewed.json').read_text())
post = json.loads((OUT / 'inventory-after.json').read_text())
summary = json.loads((OUT / 'summary.json').read_text())
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

fig, axes = plt.subplots(1, 3, figsize=(18, 8), gridspec_kw={'width_ratios': [1.25, 1, 1]})
labels = sorted(set(pre['frontend_classes']) | set(post['frontend_classes']),
                key=lambda c: pre['frontend_classes'].get(c, {}).get('functions', 0), reverse=True)
for ax, field, title in zip(axes[:2], ('functions', 'diagnostics'), ('Functions with frontend blockers', 'Frontend diagnostics')):
    a = [pre['frontend_classes'].get(k, {}).get(field, 0) for k in labels]
    b = [post['frontend_classes'].get(k, {}).get(field, 0) for k in labels]
    ax.barh([i-.19 for i in range(len(labels))], a, .36, color='#94a3b8', label='Before')
    bars = ax.barh([i+.19 for i in range(len(labels))], b, .36, color='#167b85', label='After')
    ax.bar_label(bars, padding=3, fontsize=8)
    ax.set_yticks(range(len(labels)), [s.replace('-', ' ') for s in labels] if ax == axes[0] else [])
    ax.invert_yaxis()
    ax.set_xlim(0, max(a+b) * 1.18)
    ax.set_title(title, loc='left', weight='bold')
obj = sorted(pre['object_classes'], key=lambda c: pre['object_classes'][c]['functions'], reverse=True)
a = [pre['object_classes'][k]['functions'] for k in obj]
b = [post['object_classes'].get(k, {}).get('functions', 0) for k in obj]
axes[2].barh([i-.19 for i in range(len(obj))], a, .36, color='#94a3b8')
bars = axes[2].barh([i+.19 for i in range(len(obj))], b, .36, color='#167b85')
axes[2].bar_label(bars, padding=3, fontsize=9)
axes[2].set_yticks(range(len(obj)), obj)
axes[2].invert_yaxis()
axes[2].set_xlim(0, 45)
axes[2].set_title('Compiled, nonmatching functions', loc='left', weight='bold')
for ax in axes:
    ax.spines[['top', 'right', 'left']].set_visible(False)
    ax.grid(axis='x', alpha=.15)
    ax.set_axisbelow(True)
axes[1].legend(frameon=False, loc='lower right')
fig.suptitle('Same 200 candidates | compilation blockers and object mismatches', weight='bold')
fig.text(.5, .015, 'Categories overlap. Diagnostic counts are not repair distances. Exactness comes from the object certificate.', ha='center', fontsize=10)
fig.tight_layout(rect=(0,.04,1,.96))
fig.savefig(OUT / 'histogram.png', dpi=150)
fig.savefig(OUT / 'histogram.svg')
plt.close(fig)
print(json.dumps({k:v for k,v in summary.items() if k not in ('remaining_frontend', 'remaining_objects', 'logged_attempts_by_run')}, indent=2))
