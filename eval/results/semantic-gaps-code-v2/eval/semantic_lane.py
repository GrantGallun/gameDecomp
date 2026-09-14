"""Fixed target-led semantic panel adapter for the active repair kernel.

Reuses the DAG ABI/seed machinery and differential runner. No candidate-selected
inputs, reference bodies, resynchronization acceptance, or semantic promotions.
"""
import hashlib
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from solver import mips_differential as differential, project_headers, workspace


def return_producers(run, registers):
    return [event.to_dict() for event in run.trace
            if any(name in registers for name,value,origin in event.writes)][-4:]


def callee_feedback(run):
    """Bound model-facing nested traces; complete call records stay in audits."""
    rows = []
    for call in run.concrete_calls[:2]:
        execution = call['execution']
        trace = execution.get('instruction_trace',[])
        rows.append({**{key:call[key] for key in ('callee','ordinal','instruction','arguments','authority','identity')},
            'execution':{key:execution.get(key) for key in ('status','error','return_values','abi_violations','instruction_count','write_count')},
            'instruction_prefix':trace[:6],
            'instruction_suffix':trace[max(6,len(trace)-6):],
            'omitted_trace_events':max(0,execution.get('instruction_trace_count',len(trace))-12)})
    return {'calls':rows,'omitted_calls':max(0,len(run.concrete_calls)-len(rows))}


class DeferredPanel:
    """Prepare target-led tests only once a frontend-passing child exists.

    A failed root need not have generated normalized object dumps yet. Do not
    permanently disable semantics for children that create those artifacts.
    Retry failed initialization only when target artifacts change; a successful
    panel remains fixed for every candidate in this worker.
    """
    def __init__(self, repo, ws, function, max_cases=64, max_steps=10000):
        self.args = (repo,ws,function,max_cases,max_steps)
        self.ws = ws
        self.panel = None
        self.failed_key = None
        self.failure = None

    @property
    def report(self):
        if self.panel is not None:
            return self.panel.report
        return self.failure or {'status':'not_initialized',
            'reason':'no frontend-passing candidate evaluated', 'authoritative':False}

    def __call__(self,state):
        if not state.attempt.compiled or (state.attempt.frontend or {}).get('passed') is not True:
            return None
        if self.panel is None:
            key = tuple((name,hashlib.sha256((self.ws/name).read_bytes()).hexdigest()
                         if (self.ws/name).is_file() else None)
                        for name in ('target.o','target_object_dump_normalized.s'))
            if self.failed_key != key:
                try:
                    panel = Panel(*self.args)
                    if panel is None:
                        raise ValueError('semantic panel factory returned no evaluator')
                    self.panel = panel
                except (OSError,ValueError) as exc:
                    self.failed_key = key
                    self.failure = {'status':'unavailable','reason':str(exc),'authoritative':False}
            if self.panel is None:
                return {**self.failure,'source_sha256':hashlib.sha256(state.source.encode()).hexdigest()}
        return self.panel(state)


class Panel:
    def __init__(self, repo, ws, function, max_cases=64, max_steps=10000, exploration_cases=5000,
                 callee_environment=None):
        from eval import dag_pipeline_pilot as dag
        from solver import callee_execution
        if min(max_cases,max_steps,exploration_cases) <= 0:
            raise ValueError('semantic budgets must be positive')
        self.ws, self.function, self.max_steps = ws, function, max_steps
        self.cache = {}
        self.target = workspace.semantic_assembly((ws/'target_object_dump_normalized.s').read_text(),ws/'target.o')
        self.abi = dag.prototype_info(repo,function,self.target)
        calls = project_headers.called_functions(self.target)
        self.arities, contracts = dag._call_contracts(repo,calls)
        if callee_environment is None:
            self.callee_environment, callee_report = callee_execution.load_binary_leaves(repo,calls)
        else:
            self.callee_environment, callee_report = callee_environment, []
        environment_manifest = self.callee_environment.manifest()
        for name, leaf in self.callee_environment.leaves.items():
            if leaf.word_pair_multiply:
                self.arities[name] = 4
                contracts[name] = {'arity_known':True, 'arity_source':'ROM-bound word-pair instruction dialect',
                                   'return_registers':['v0','v1']}
        self.debt = ['finite synthetic inputs; not universal equivalence']
        opaque = [n for n in calls if n not in self.callee_environment.leaves]
        if opaque: self.debt.append('opaque callees: '+', '.join(opaque))
        if self.callee_environment.leaves:
            self.debt.append('bounded integer leaf execution; frame bounds do not prove C object extents; nested calls/relocations unsupported')
        if self.callee_environment.outputs:
            self.debt.append('explicit output-buffer environment assumptions, not hardware emulation')
        if any(i.opcode=='jalr' for i in differential.Program.parse(function,self.target).instructions):
            self.debt.append('indirect callees lack concrete implementation/arity contracts')
        self.debt.extend(self.abi.get('issues',[]))
        self.debt.extend('unknown call arity: '+name for name,value in contracts.items() if not value['arity_known'])
        self.returns = tuple(self.abi['return_registers'])
        arguments = dict(call_arities=self.arities,return_registers=self.returns,
            mutable_entry_registers=tuple(self.abi['mutable_scalar_registers']),
            pointer_entry_registers=tuple(n for n in self.abi.get('pointer_registers',[]) if n!='a0'),
            max_steps=max_steps,callee_environment=self.callee_environment)
        explored = differential.explore_coverage(self.target,dag._seed_cases(function,self.abi),
            target_name=function,max_cases=exploration_cases,**arguments)
        self.execution_obstructions = explored.execution_obstructions
        if self.execution_obstructions:
            self.debt.append('target exploration encountered unsupported execution; passing selected cases do not discharge it')
        seeds = tuple(case for case,run in zip(explored.cases,explored.runs)
                      if run.status in differential.COMPLETED_STATUSES)
        self.cases = ()
        if seeds:
            stress = differential.build_semantic_stress_panel(self.target,seeds,
                target_name=function,max_cases=max(max_cases,len(seeds)),**arguments)
            self.cases = stress.cases
        self.identity = hashlib.sha256(json.dumps({'target':self.target,'cases':[asdict(c) for c in self.cases],
            'abi':self.abi,'arities':self.arities,'steps':max_steps,
            'callee_environment':environment_manifest,
            'runner':hashlib.sha256(Path(differential.__file__).read_bytes()).hexdigest()},sort_keys=True).encode()).hexdigest()
        self.report = {'panel_sha256':self.identity,'abi':self.abi,'call_arities':self.arities,
            'callee_environment':environment_manifest,'callee_admission':callee_report,
            'cases':[asdict(c) for c in self.cases], 'target_exploration':explored.report.to_dict(),
            'execution_obstructions':list(self.execution_obstructions),
            'exploration_case_budget':exploration_cases,
            'authority':'target-led diagnostic tests, not semantic proof','debt':self.debt}

    def __call__(self,state):
        from eval import differential_repair_pilot as repair
        from solver import callee_execution
        from solver import semantic_gradient
        if not state.attempt.compiled or (state.attempt.frontend or {}).get('passed') is not True:
            return None
        key = hashlib.sha256(state.source.encode()).hexdigest()
        if key in self.cache: return self.cache[key]
        if not self.cases:
            return {'status':'unavailable','source_sha256':key,'panel_sha256':self.identity,
                    'reason':'no completed target cases','debt':self.debt,'authoritative':False}
        obj = state.object_path
        assembly = workspace.semantic_assembly(obj.with_name(obj.stem+'_object_dump_normalized.s').read_text(),obj)
        rows = differential.run_suite(self.target,assembly,self.cases,target_name=self.function,
            candidate_name=self.function+'-candidate',call_arities=self.arities,
            return_registers=self.returns,max_steps=self.max_steps,callee_environment=self.callee_environment)
        counts = dict(Counter(row.status for row in rows))
        failed = [row for row in rows if row.status != 'passed']
        feedback = []
        distinct = set()
        for case,row in zip(self.cases,rows):
            if row.status != 'failed': continue
            shape = tuple(row.reasons)
            if shape in distinct: continue
            distinct.add(shape)
            packet = {'input':asdict(case),'reasons':row.reasons,
                'target_return':row.target.return_values,'candidate_return':row.candidate.return_values,
                'causal_feedback':differential.causal_feedback(row,max_steps=10)}
            if row.target.concrete_calls or row.candidate.concrete_calls:
                packet['concrete_callee_executions'] = {
                    side:callee_feedback(run) for side,run in [('target',row.target),('candidate',row.candidate)]}
            if row.target.return_values != row.candidate.return_values:
                # The old first-divergence formatter may name a harmless write
                # permutation. Show the actual failing gate and its producers.
                packet['return_producers'] = {}
                for side,run in [('target',row.target),('candidate',row.candidate)]:
                    packet['return_producers'][side] = return_producers(run,self.returns)
            feedback.append(packet)
            if len(feedback) == 3: break
        status = ('observed_failure' if failed else 'observed_pass_with_execution_debt'
                  if self.execution_obstructions else 'observed_pass')
        result = {'status':status,
            'source_sha256':key,'panel_sha256':self.identity,'counts':counts,'total':len(rows),
            'semantic_key':list(repair.behavior_key(rows,state.attempt)[:-2]),
            'passed_case_indices':[i for i,row in enumerate(rows) if row.status=='passed'],
            'authoritative':False,'debt':self.debt,
            'execution_obstructions':list(self.execution_obstructions),
            'callee_environment':self.callee_environment.manifest(),
            'callee_source_contracts':callee_execution.source_contracts(state.source,self.callee_environment),
            'target_coverage':differential.coverage_report(differential.Program.parse(self.function,self.target),[r.target for r in rows]).to_dict(),
            'candidate_coverage':differential.coverage_report(differential.Program.parse(self.function,assembly),[r.candidate for r in rows]).to_dict(),
            'operation_gradient':semantic_gradient.render_operation_gradient(rows,state.source,max_clusters=3),
            'feedback':feedback}
        self.cache[key] = result
        obj.with_suffix('.semantic-lane.json').write_text(json.dumps(result,indent=2)+'\n')
        return result
