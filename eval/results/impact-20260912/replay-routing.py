"""Compare routing on one saved immutable checkpoint; no campaign DB access."""
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from eval.completion_campaign import PROFILES
from solver import repair_queue as revised

directory = Path(__file__).parent
state = json.loads((directory / 'routing-state.json').read_text())
frozen = Path(state['config']['project']) / 'solver/repair_queue.py'
spec = importlib.util.spec_from_file_location('frozen_routing_audit', frozen)
old = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = old
spec.loader.exec_module(old)
before, selected_before = old.project(state, PROFILES)
after, selected_after = revised.project(state, PROFILES)
changed = []
for name, node in state['nodes'].items():
    prior, current = before['work_items'].get(name), after['work_items'].get(name)
    if prior != current:
        changed.append({'function': name, 'target_bytes': 4 * (node.get('instruction_count') or 0),
                        'before': prior, 'after': current})
inconclusive = [name for name,n in state['nodes'].items()
                if old.lane(n).value == 'byte' and revised.lane(n).value == 'environment']
obstructions = Counter()
for name in inconclusive:
    groups = state['nodes'][name]['semantic_validation'].get('outcome_accounting', {}).get('inconclusive_reason_groups', [])
    for group in groups:
        obstructions.update(group.get('reasons', []))
report = {
    'before_eligible': len(before['work_items']), 'after_eligible': len(after['work_items']),
    'all_evidence_keys_unchanged': all(old.evidence_key(n) == revised.evidence_key(n) for n in state['nodes'].values()),
    'eligible_sets_unchanged': before['work_items'].keys() == after['work_items'].keys(),
    'selected_before': selected_before, 'selected_after': selected_after,
    'inconclusive_rerouted_functions': len(inconclusive),
    'inconclusive_rerouted_bytes': sum(4 * state['nodes'][n]['instruction_count'] for n in inconclusive),
    'queued_model_profiles_replaced': sum(c['before'] and c['before']['profile'] in {'schema_patch','reasoned_alternative'}
                                          and c['after'] and c['after']['profile'] in {'local_rewrites','deeper_composition'}
                                          for c in changed),
    'prior_completed_profiles_not_reopened': all(
        current['profile'] not in {j['profile'] for j in state['nodes'][name].get('jobs', [])
                                   if j.get('evidence_key') == current['evidence_key']}
        for name,current in after['work_items'].items()),
    'unchanged_non_environment_lane_profiles': all(
        revised.next_profile(n,state['config']['model_calls'],PROFILES) == old.next_profile(n,state['config']['model_calls'],PROFILES)
        for name,n in state['nodes'].items() if name not in inconclusive),
    'obstruction_function_counts': dict(obstructions),
    'shared_issues': [r for r in after['shared_issues'].values()
                      if r['identity'].get('comparison_status') == 'inconclusive'],
    'changes': changed,
}
(directory/'routing-comparison.json').write_text(json.dumps(report, indent=2))
print(json.dumps({k:v for k,v in report.items() if k not in {'changes','shared_issues'}},indent=2))
