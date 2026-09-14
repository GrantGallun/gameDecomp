"""Inspect retained finite-panel writes without assuming unspecified memory."""
import json
from pathlib import Path
from collections import Counter

folder = Path(__file__).resolve().parent
panel = json.loads((folder / 'round1-results/panel-report.json').read_bytes())
selectors = Counter()
for case in panel['cases']:
    memory = {}
    for offset, width, value in case['player_writes']:
        for byte in range(width):
            memory[offset + byte] = (value >> (8 * (width - byte - 1))) & 255
    selectors[str(memory[28] * 256 + memory[29]) if 28 in memory and 29 in memory else 'not explicitly set'] += 1
report = {'case_count': len(panel['cases']), 'explicit_sprite_index_counts': dict(selectors),
          'panel_sha256': panel['panel_sha256'],
          'scope': 'Effective explicit case writes at arg0+0x1c only; does not infer values on the alternate forced-selector branch',
          'exploration_trials': panel.get('exploration_trials'),
          'stress_work_stop_reasons': panel.get('stress_work', {}).get('stop_reasons')}
(folder / 'selector-diagnostics/normal-selector-audit.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({k: report[k] for k in ('case_count', 'explicit_sprite_index_counts', 'stress_work_stop_reasons')}))
