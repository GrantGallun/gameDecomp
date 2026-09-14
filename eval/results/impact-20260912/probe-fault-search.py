"""Private target-only comparison; no model calls or canonical DB access."""
import inspect,json,time
from pathlib import Path
from eval.semantic_lane import Panel
from eval import dag_pipeline_pilot as dag
from solver import mips_differential as d

root=Path(__file__).resolve().parent
old=Path('eval/results/partial-reconstruction-20260912')
state=json.loads((old/'popup-v3/state.json').read_text())
version=json.loads((old/'popup-v3/version-0001.json').read_text())
panel=Panel(Path(state['repo']),Path('/home/grant/decomp/partial-memory-replay-20260912'),
 state['function'],64,10000,256,header_source=version['manifest']['source'])
args=dict(target_name=state['function'],call_arities=panel.arities,return_registers=panel.returns,
 mutable_entry_registers=tuple(panel.abi['mutable_scalar_registers']),
 pointer_entry_registers=tuple(n for n in panel.abi.get('pointer_registers',[]) if n!='a0'),
 max_cases=5000,max_steps=10000,max_total_steps=1280000,callee_environment=panel.callee_environment)
seed=dag._seed_cases(state['function'],panel.abi)
source=inspect.getsource(d.explore_coverage)
source=source.replace('if phase == "directed":','if phase == "directed" and base_run.status != "memory_fault":')
source=source.replace('(phase == "broad" and discovery_novel and','((phase == "broad" or base_run.status == "memory_fault") and discovery_novel and')
ns=dict(d.__dict__);exec(source,ns)
rows={}
original_mutations=d._iter_mutations
def fair_memory(program,case,run,**kwargs):
 from collections import deque
 category=kwargs.get('category')
 if category not in (None,'memory'):
  yield from original_mutations(program,case,run,**kwargs);return
 groups={}
 for mutation in original_mutations(program,case,run,**{**kwargs,'category':'memory'}):
  groups.setdefault(d._mutation_dimension(case,mutation),deque()).append(mutation)
 streams=deque(groups.values())
 while streams:
  stream=streams.popleft();yield stream.popleft()
  if stream:streams.append(stream)
 if category is None:
  for family in ('scalar','pointer','call','filler'):
   yield from original_mutations(program,case,run,**{**kwargs,'category':family})
def compact(writes):
 seen=set();out=[]
 for location,width,value in reversed(writes):
  key=(location,width)
  if key not in seen:out.append((location,width,value));seen.add(key)
 return tuple(reversed(out))
dedup_source=inspect.getsource(d.explore_coverage).replace(
 'case.seed, case.player_writes, case.global_writes,',
 'case.seed, compact(case.player_writes), compact(case.global_writes),')
dedup_ns={**d.__dict__,'compact':compact};exec(dedup_source,dedup_ns)
for name,fn in [('baseline',d.explore_coverage),('dedup',dedup_ns['explore_coverage'])]:
 if name=='fair_memory':d._iter_mutations=fair_memory
 start=time.perf_counter();r=fn(panel.target,seed,**args)
 rows[name]={'wall_seconds':time.perf_counter()-start,**r.to_dict()}
 print(name,json.dumps({k:rows[name][k] for k in ('wall_seconds','attempted_cases','trial_status_counts','executed_steps')}),rows[name]['coverage']['instruction_coverage'],rows[name]['coverage']['branch_edge_coverage'],flush=True)
(root/'fault-search-probe.json').write_text(json.dumps(rows,indent=2))
