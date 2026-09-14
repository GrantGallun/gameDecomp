"""Read-only selection of large, currently noncompiling campaign candidates."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval import campaign_state

state = campaign_state.read(ROOT / 'eval/results/resume-pipeline-20260908/campaign.json')
selected = sorted(((n, row) for n, row in state['nodes'].items()
    if row.get('attempt_id') and (row.get('residual') or {}).get('compiled') is False),
    key=lambda item: -(item[1].get('instruction_count') or 0))[:8]
rows = []
for name, node in selected:
    path = Path(__file__).parent / (name + '.original.c')
    path.write_text(Path(node['source']).read_text())
    rows.append({'function': name, 'attempt': node['attempt_id'], 'instructions': node['instruction_count'],
                 'source': str(path), 'source_sha256': node['source_sha256'], 'visits': len(node['jobs'])})
(Path(__file__).parent / 'selection.json').write_text(json.dumps(rows, indent=2))
print(json.dumps(rows))
