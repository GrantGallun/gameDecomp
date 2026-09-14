"""Measure exact shadow-write case identities on a private target panel."""
import inspect,json,time,hashlib
from collections import Counter
from pathlib import Path
from eval.semantic_lane import Panel
from solver import mips_differential as d, workspace

def compact(writes):
 seen=set();out=[]
 for location,width,value in reversed(writes):
  key=(location,width)
  if key not in seen:out.append((location,width,value));seen.add(key)
 return tuple(reversed(out))
def key(case):
 return(case.seed,compact(case.player_writes),compact(case.global_writes),case.entry_registers,case.call_returns)

root=Path(__file__).resolve().parent
old=Path('eval/results/partial-reconstruction-20260912')
state=json.loads((old/'popup-v3/state.json').read_text())
version=json.loads((old/'popup-v3/version-0001.json').read_text())
panel=Panel(Path(state['repo']),Path('/home/grant/decomp/partial-memory-replay-20260912'),
 state['function'],64,10000,256,header_source=version['manifest']['source'])
args=dict(target_name=state['function'],call_arities=panel.arities,return_registers=panel.returns,
 mutable_entry_registers=tuple(panel.abi['mutable_scalar_registers']),
 pointer_entry_registers=tuple(n for n in panel.abi.get('pointer_registers',[]) if n!='a0'),
 max_cases=64,max_steps=10000,max_total_steps=1280000,max_trials=256,max_generated=4096,
 callee_environment=panel.callee_environment)
program=d.Program.parse(state['function'],panel.target)
seeds=[]
for row in panel.report['exploration_budget_phases'][0]['exploration']['selected_cases']:
 case=d.TestCase(**{k:tuple(tuple(v) for v in val) if isinstance(val,list) else val for k,val in row.items()})
 run=d.execute_case(program,case,call_arities=panel.arities,return_registers=panel.returns,callee_environment=panel.callee_environment)
 if run.status in d.COMPLETED_STATUSES:seeds.append(case)
source=inspect.getsource(d.build_semantic_stress_panel).replace(
 'return _stress_input_identity(case)',
 'return (case.seed, case.player_writes, case.global_writes, case.entry_registers, case.call_returns)')
ns=dict(d.__dict__);exec(source,ns)
obj=panel.ws / Path(version['object']).name
candidate=workspace.semantic_assembly(obj.with_name(obj.stem+'_object_dump_normalized.s').read_text(),obj)
rows={}
for name,fn in [('baseline',ns['build_semantic_stress_panel']),('dedup',d.build_semantic_stress_panel)]:
 start=time.perf_counter();r=fn(panel.target,tuple(seeds),**args)
 rows[name]={'wall_seconds':time.perf_counter()-start,'unique_input_count':len({key(c) for c in r.cases}),**r.to_dict()}
 compared=d.run_suite(panel.target,candidate,r.cases,target_name=state['function'],candidate_name='unfinished-guard',
  call_arities={**panel.arities,'__gd_unfinished':1},return_registers=panel.returns,max_steps=10000,
  callee_environment=panel.callee_environment)
 rows[name]['unfinished_candidate_verdicts']=dict(Counter(row.status for row in compared))
 rows[name]['distinct_target_call_sequences']=len({tuple((c.callee,c.arguments) for c in run.calls) for run in r.runs})
 rows[name]['runner_sha256']=hashlib.sha256(Path(d.__file__).read_bytes()).hexdigest()
 print(name,rows[name]['wall_seconds'],rows[name]['unique_input_count'],len(r.cases),flush=True)
(root/'input-dedup-probe.json').write_text(json.dumps(rows,indent=2))
