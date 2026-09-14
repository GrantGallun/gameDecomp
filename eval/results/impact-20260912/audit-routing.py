import json
from collections import Counter
from pathlib import Path
from eval.campaign_state import read
from eval.completion_campaign import PROFILES
from solver.repair_queue import project, lane

root = Path('/mnt/c/Code/gameDecomp')
state = read(root / 'eval/results/resume-pipeline-20260908/campaign.json')
projection, selected = project(state, PROFILES)
counts = Counter()
examples = {}
for name, n in state['nodes'].items():
    sem = n.get('semantic_validation') or {}
    key = (n['status'], sem.get('status'), lane(n).value)
    counts[key] += 1
    if sem.get('status') == 'inconclusive':
        examples[name] = {k:n.get(k) for k in ('instruction_count','source_sha256','semantic_validation','jobs')}
report = {'config': state['config'], 'counts': [{'group':k,'count':v} for k,v in counts.items()],
          'selected': selected, 'inconclusive_examples': examples,
          'queue': dict(Counter((r['lane'],r['profile']) for r in projection['work_items'].values()))}
report['queue'] = [{'group':k,'count':v} for k,v in report['queue'].items()]
(Path(__file__).parent / 'routing-before.json').write_text(json.dumps(report, indent=2))
(Path(__file__).parent / 'routing-state.json').write_text(json.dumps(state))
print(json.dumps({k:v for k,v in report.items() if k not in ('config','inconclusive_examples')},indent=2))
print('inconclusive functions', len(examples))
