"""Read-only yield and failure census of one immutable live checkpoint."""
import collections
import json
from pathlib import Path
import sqlite3
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FROZEN = ROOT/'eval/results/resume-pipeline-20260908/code'
RUN = ROOT/'eval/results/combined-frontier-20260926/frontier'
NATIVE = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
sys.path.insert(0, str(FROZEN))
from eval import campaign_state, completion_campaign
from solver import repair_queue

pointer = json.loads((NATIVE/'campaign.json').read_bytes())
with sqlite3.connect((NATIVE/pointer['store']).resolve().as_uri()+'?mode=ro', uri=True) as db:
    state = campaign_state._hydrate(db, pointer)
baseline = json.loads((RUN/'baseline.json').read_bytes())
baseline_pointer = json.loads((RUN.parent/'frontend-payload/campaign.before.json').read_bytes())
queue, selected = repair_queue.project(state, completion_campaign.PROFILES)
revision = repair_queue.binary_input_revision(state)
lanes = collections.Counter()
faults = collections.Counter()
semantic = collections.Counter()
reasons = collections.Counter()
parked = collections.Counter()
profiles = collections.Counter()
model_selected = []
exhausted = []
examples = collections.defaultdict(list)
exact_states = {'object_exact', 'integrated', 'function_exact_pending_integration'}
for name, node in state['nodes'].items():
    lane = repair_queue.lane(node).value
    lanes[lane] += 1
    if node['status'] in exact_states:
        continue
    res = node.get('residual') or {}
    sem = node.get('semantic_validation') or {}
    if len(examples[lane]) < 8:
        examples[lane].append({'function':name, 'score':node.get('score'),
            'compiled':res.get('compiled'), 'frontend':(res.get('frontend') or {}).get('passed'),
            'faults':res.get('faults'), 'semantic_status':sem.get('status'),
            'semantic_reason':sem.get('reason'), 'semantic_counts':sem.get('counts'),
            'semantic_limits':{k:sem[k] for k in ('errors','target_counts','candidate_counts','exploration') if k in sem},
            'blocker':node.get('blocker'), 'last_profiles':[j.get('profile') for j in node.get('jobs', [])[-6:]]})
    if node['status'] == 'parked':
        parked[(node.get('blocker') or {}).get('status','unknown')] += 1
    for key, count in (res.get('faults') or {}).items():
        if count:
            faults[key] += 1
    if sem:
        semantic[sem.get('status', 'missing')] += 1
        if sem.get('reason'):
            reasons[str(sem['reason'])[:500]] += 1
    profile = repair_queue.next_profile(node, state['config']['model_calls'], completion_campaign.PROFILES, revision)
    if profile:
        profiles[profile['name'].split('@')[0]] += 1
        if profile.get('model'):
            model_selected.append({'function':name,'lane':lane,'profile':profile['name']})
    elif node['status'] == 'pending':
        exhausted.append({'function':name,'lane':lane,'score':node.get('score')})

previous = {name:row.get('score') for name,row in baseline['nodes'].items()}
yield_by = collections.defaultdict(lambda: collections.Counter())
events = []
for path in sorted(RUN.glob('batch-*/canary-controller.log')):
    for line in path.read_text(errors='replace').splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row,dict) or not row.get('function'):
            continue
        name, score = row['function'], row.get('score')
        before = previous.get(name)
        profile = row.get('profile', 'unknown').split('@')[0]
        stats = yield_by[profile]
        stats['jobs'] += 1
        if isinstance(before,(int,float)) and isinstance(score,(int,float)):
            change = score-before
            stats['flat_score' if abs(change)<0.0005 else 'higher_score' if change>0 else 'lower_score'] += 1
        else:
            stats['no_score_comparison'] += 1
        previous[name] = score
        stats['exact_results'] += row.get('status') in exact_states
        for k,v in (row.get('performance') or {}).items():
            if isinstance(v,(int,float)) and (k.endswith('_seconds') or k in ('compile_hits','compile_misses','model_calls')):
                stats[k] += v
        events.append(row)

prior_exact = {n for n,r in baseline['nodes'].items() if r['status'] in exact_states}
now_exact = {n for n,r in state['nodes'].items() if r['status'] in exact_states}
metric_delta = {k:v-baseline_pointer.get('fast_metrics',{}).get(k,0)
    for k,v in state.get('fast_metrics',{}).items() if isinstance(v,(int,float))
    and (k.endswith('_seconds') or k in ('completed_items','improved_items','exact_items','compile_hits','compile_misses','model_calls'))}
report = {'recorded_at':time.time(), 'commit':pointer['commit'], 'summary':pointer['summary'],
    'elapsed_wall_seconds':time.time()-json.loads((RUN/'progress.json').read_bytes())['started_at'],
    'exact_gains':sorted(now_exact-prior_exact),'exact_losses':sorted(prior_exact-now_exact),
    'new_vs_prior_raw_exact_ledgers': sorted(now_exact-prior_exact-set(baseline['research_exact'])-set(baseline['campaign_attempt_exact'])),
    'metrics_delta':metric_delta, 'lanes':lanes, 'faults_overlapping_unsolved_functions':faults,
    'unsolved_semantic_statuses':semantic, 'unsolved_semantic_reasons':reasons,
    'parked_reasons':parked,'eligible_profiles':profiles,'selected_next':selected,
    'model_selected_count':len(model_selected),'model_selected':model_selected,
    'exhausted_count':len(exhausted),'exhausted_lanes':collections.Counter(r['lane'] for r in exhausted),
    'exhausted_examples':exhausted[:30], 'examples':dict(examples),
    'logged_completed_jobs':len(events),'yield_by_profile':dict(yield_by),
    'limits':'Captured checkpoint and log tail have separate read times. Score changes are accepted-result deltas, not proof of semantic progress. No state, source, ledger or solver mutations.'}
(HERE/'audit.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k not in ('examples','model_selected','exhausted_examples')},indent=2))
