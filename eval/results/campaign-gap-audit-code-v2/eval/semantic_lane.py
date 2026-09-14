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


class Panel:
    def __init__(self, repo, ws, function, max_cases=64, max_steps=10000, exploration_cases=5000):
        from eval import dag_pipeline_pilot as dag
        if min(max_cases,max_steps,exploration_cases) <= 0:
            raise ValueError('semantic budgets must be positive')
        self.ws, self.function, self.max_steps = ws, function, max_steps
        self.cache = {}
        self.target = workspace.semantic_assembly((ws/'target_object_dump_normalized.s').read_text(),ws/'target.o')
        self.abi = dag.prototype_info(repo,function,self.target)
        calls = project_headers.called_functions(self.target)
        self.arities, contracts = dag._call_contracts(repo,calls)
        self.debt = ['finite synthetic inputs; not universal equivalence']
        if calls: self.debt.append('opaque callees: '+', '.join(calls))
        if any(i.opcode=='jalr' for i in differential.Program.parse(function,self.target).instructions):
            self.debt.append('indirect callees lack concrete implementation/arity contracts')
        self.debt.extend(self.abi.get('issues',[]))
        self.debt.extend('unknown call arity: '+name for name,value in contracts.items() if not value['arity_known'])
        self.returns = tuple(self.abi['return_registers'])
        arguments = dict(call_arities=self.arities,return_registers=self.returns,
            mutable_entry_registers=tuple(self.abi['mutable_scalar_registers']),
            pointer_entry_registers=tuple(n for n in self.abi.get('pointer_registers',[]) if n!='a0'),
            max_steps=max_steps)
        explored = differential.explore_coverage(self.target,dag._seed_cases(function,self.abi),
            target_name=function,max_cases=exploration_cases,**arguments)
        seeds = tuple(case for case,run in zip(explored.cases,explored.runs)
                      if run.status in differential.COMPLETED_STATUSES)
        self.cases = ()
        if seeds:
            stress = differential.build_semantic_stress_panel(self.target,seeds,
                target_name=function,max_cases=max(max_cases,len(seeds)),**arguments)
            self.cases = stress.cases
        self.identity = hashlib.sha256(json.dumps({'target':self.target,'cases':[asdict(c) for c in self.cases],
            'abi':self.abi,'arities':self.arities,'steps':max_steps,
            'runner':hashlib.sha256(Path(differential.__file__).read_bytes()).hexdigest()},sort_keys=True).encode()).hexdigest()
        self.report = {'panel_sha256':self.identity,'abi':self.abi,'call_arities':self.arities,
            'cases':[asdict(c) for c in self.cases], 'target_exploration':explored.report.to_dict(),
            'exploration_case_budget':exploration_cases,
            'authority':'target-led diagnostic tests, not semantic proof','debt':self.debt}

    def __call__(self,state):
        from eval import differential_repair_pilot as repair
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
            return_registers=self.returns,max_steps=self.max_steps)
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
            if row.target.return_values != row.candidate.return_values:
                # The old first-divergence formatter may name a harmless write
                # permutation. Show the actual failing gate and its producers.
                packet['return_producers'] = {}
                for side,run in [('target',row.target),('candidate',row.candidate)]:
                    packet['return_producers'][side] = return_producers(run,self.returns)
            feedback.append(packet)
            if len(feedback) == 3: break
        result = {'status':'observed_pass' if not failed else 'observed_failure',
            'source_sha256':key,'panel_sha256':self.identity,'counts':counts,'total':len(rows),
            'semantic_key':list(repair.behavior_key(rows,state.attempt)[:-2]),
            'passed_case_indices':[i for i,row in enumerate(rows) if row.status=='passed'],
            'authoritative':False,'debt':self.debt,
            'target_coverage':differential.coverage_report(differential.Program.parse(self.function,self.target),[r.target for r in rows]).to_dict(),
            'candidate_coverage':differential.coverage_report(differential.Program.parse(self.function,assembly),[r.candidate for r in rows]).to_dict(),
            'operation_gradient':semantic_gradient.render_operation_gradient(rows,state.source,max_clusters=3),
            'feedback':feedback}
        self.cache[key] = result
        obj.with_suffix('.semantic-lane.json').write_text(json.dumps(result,indent=2)+'\n')
        return result
