"""Read the native checkpoint and select a candidate, never target source."""
import collections
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
FROZEN = ROOT / 'eval/results/resume-pipeline-20260908/code'
sys.path.insert(0, str(FROZEN))
from eval import campaign_state
from solver import repair_queue

state_path = Path('/home/grant/decomp/runs/resume-pipeline-20260908/campaign.json')
state = campaign_state.read(state_path)
print(json.dumps({'config': {k: state['config'].get(k) for k in ('project', 'repo', 'db', 'model', 'scheduler')},
                  'pointer': json.loads(state_path.read_bytes())['summary'],
                  'inflight': bool(state.get('fast_inflight') or state.get('inflight')),
                  'lanes': dict(collections.Counter(repair_queue.lane(n).value for n in state['nodes'].values()))}, indent=2))
candidates = [(n.get('instruction_count', 0), name, n) for name, n in state['nodes'].items()
              if n.get('source') and n.get('residual', {}).get('compiled') and n.get('status') not in
              {'object_exact', 'integrated', 'parked'}]
chosen = sorted(candidates, key=lambda row: (abs(row[0] - 30), row[1]))[:8]
out = [{'function': name, 'instructions': count, 'node': n} for count, name, n in chosen]
(Path(__file__).parent / 'candidates.json').write_text(json.dumps(out, indent=2))
print(json.dumps([{'function': r['function'], 'instructions': r['instructions'],
                   'source': r['node']['source'], 'lane': repair_queue.lane(r['node']).value} for r in out], indent=2))
