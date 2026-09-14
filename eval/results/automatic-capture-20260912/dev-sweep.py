"""Read an immutable drained checkpoint, exercise runtime sweep only in memory."""
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from eval import campaign_runtime, campaign_state

root = Path('/mnt/c/Code/gameDecomp')
campaign = root / 'eval/results/resume-pipeline-20260908'
audit = root / 'eval/results/automatic-capture-20260912'
pointer = json.loads((campaign/'campaign.json').read_bytes())
store = campaign / pointer['store']
with sqlite3.connect(f'file:{store}?mode=ro', uri=True) as conn:
    manifest = conn.execute('SELECT manifest FROM commits WHERE id=4250').fetchone()[0]
pointer.update(commit=4250, sha256=hashlib.sha256(manifest).hexdigest(), store=str(store))
active_code = Path(campaign_runtime.__file__).resolve().parents[1]
output = audit / (('dev-' if active_code == root else 'staged-dev-') + str(time.time_ns()))
output.mkdir()
private_pointer = output/'checkpoint-pointer.json'
private_pointer.write_text(json.dumps(pointer))
state = campaign_state.read(private_pointer)
assert not state.get('fast_inflight') and not state.get('inflight')
plans = audit / 'capture-plans.json'
state['config']['runtime_capture_plan_sha256'] = campaign_runtime.sha(plans)
before = {n: {'status': state['nodes'][n]['status'], 'binding': campaign_runtime.binding(state['nodes'][n])}
          for n in {i['plan']['function'] for i in json.loads(plans.read_bytes())['plans']}}
modules = {p: campaign_runtime.sha(active_code/p) for p in ('eval/campaign_runtime.py', 'eval/project64_runner.py',
           'solver/project64_capture.py', 'solver/runtime_capture.py', 'eval/captured_panel.py',
           'eval/agentrepair.py', 'eval/completion_campaign.py')}
campaign_runtime.write(output/'inputs.json', {'checkpoint': pointer, 'selected': before, 'modules': modules,
                                             'active_code': str(active_code),
                                             'plan_sha256': campaign_runtime.sha(plans), 'live_state_saved': False})
print(json.dumps({'output': str(output), 'selected': before}), flush=True)
started = time.monotonic()
changed = campaign_runtime.sweep(state, repo=Path(state['config']['repo']), artifacts=output/'artifacts',
                                plan_path=plans, checkpoint=4250)
result = {'checkpoint': 4250, 'elapsed_seconds': time.monotonic()-started, 'live_state_saved': False,
          'changed_in_memory': changed, 'runtime_capture': state['runtime_capture'],
          'after': {n: {'status': state['nodes'][n]['status'], 'binding': campaign_runtime.binding(state['nodes'][n])} for n in before}}
campaign_runtime.write(output/'result.json', result)
print(json.dumps({'output': str(output), 'elapsed_seconds': result['elapsed_seconds'],
                  'results': {k: {'status': v['status'], 'counts': v['counts'], 'error': v.get('error')}
                              for k,v in state['runtime_capture']['results'].items()}}), flush=True)
