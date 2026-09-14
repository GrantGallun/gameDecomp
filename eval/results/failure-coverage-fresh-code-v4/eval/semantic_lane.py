"""Fixed target-led semantic panel adapter for the active repair kernel.

Reuses the DAG ABI/seed machinery and differential runner. No candidate-selected
inputs, reference bodies, resynchronization acceptance, or semantic promotions.
"""
import hashlib
import json
import re
import subprocess
from collections import Counter
from dataclasses import asdict, replace
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


def opaque_stack_obligations(result):
    """Expose unmodeled stack pointees without equating relocated objects.

    Opaque call hashes include stack addresses, not their pointee bytes. Thus
    different labels can create artificial return/clobber differences, while
    equal labels can hide different inputs. Concrete calls and explicitly
    normalized output/string arguments do not have raw stack labels here.
    """
    obligations = []
    for side, run in [('target', result.target), ('candidate', result.candidate)]:
        other = result.candidate if side == 'target' else result.target
        peers = {call.ordinal: call for call in other.calls}
        for call in run.calls:
            peer = peers.get(call.ordinal)
            for index, label in enumerate(call.arguments):
                if label != 'stack' and not label.startswith('stack+'):
                    continue
                peer_label = (peer.arguments[index] if peer and peer.callee == call.callee
                              and index < len(peer.arguments) else None)
                obligations.append({'side': side, 'call_ordinal': call.ordinal,
                    'callee': call.callee, 'argument_word': index, 'address': label,
                    'peer_address': peer_label, 'address_differs': peer_label != label,
                    'kind': 'opaque-stack-pointee-unmodeled',
                    'authority': 'executed call argument; object extent/type/alias mapping unknown',
                    'next_action': 'execute a validated callee or supply an explicit object/effect contract; '
                                   'do not force stack offsets or waive the mismatch',
                    'limitation': 'opaque return/clobber hashes depend on address labels; '
                                  'stack pointee bytes are absent from the persistent checkpoint'})
    return obligations


def indirect_call_obligations(result):
    """Surface ignored ABI words without mistaking them for proven arguments."""
    rows = []
    peers = {call.ordinal:call for call in result.candidate.calls}
    for call in result.target.calls:
        peer = peers.get(call.ordinal)
        if call.arity_known and (peer is None or peer.arity_known):
            continue
        target = {word['word']:word for word in call.abi_observations}
        candidate = {word['word']:word for word in peer.abi_observations} if peer else {}
        differences = [{'word':index,'target':target.get(index),'candidate':candidate.get(index)}
            for index in sorted(target.keys() | candidate.keys())
            if target.get(index,{}).get('label') != candidate.get(index,{}).get('label')
            or target.get(index,{}).get('available') != candidate.get(index,{}).get('available')]
        rows.append({'kind':'indirect-call-arity-unmodeled','call_ordinal':call.ordinal,
            'target_callee':call.callee,'candidate_callee':peer.callee if peer else None,
            'unclassified_abi_word_differences':differences,
            'observed_word_limit':8,
            'authority':'diagnostic ABI window, not an argument contract or semantic failure',
            'next_action':'Bind callback field/target to an independently supported ABI and effects, then rerun; '
                          'do not repair unused registers or force equal stack offsets.'})
    return rows


def classify_unknown_direct_arguments(row, contracts):
    """Do not make a source-error claim solely from guessed direct-call words.

Keep raw executions/first divergence. Never convert a discrepancy to a pass,
or excuse known argument, call order, call-time memory, return or final memory
differences. Cascaded mismatches need a separate causal audit, not this rule.
"""
    if row.status != 'failed' or row.reasons != ('external call trace or call-time memory differs',):
        return row
    if len(row.target.calls) != len(row.candidate.calls):
        return row
    unresolved = []
    for left, right in zip(row.target.calls, row.candidate.calls):
        if left.callee != right.callee or left.checkpoint_digest != right.checkpoint_digest:
            return row
        if left.arguments == right.arguments:
            continue
        contract = contracts.get(left.callee)
        if (contract is None or contract.get('arity_known') is not False
                or left.callee.startswith('indirect@')):
            return row
        unresolved.append(left.callee)
    if not unresolved:
        return row
    return replace(row, status='inconclusive', reasons=(
        'only uncontracted direct-call argument words differ: '+', '.join(sorted(set(unresolved)))+
        '; establish ABI before attributing a source bug',))


def outcome_accounting(rows, *, execution_debt=False):
    """Keep concrete disagreement separate from incomplete comparisons."""
    counts=dict(Counter(row.status for row in rows))
    failed=counts.get('failed',0)
    unresolved=sum(v for k,v in counts.items() if k not in {'passed','failed'})
    status=('observed_failure' if failed else 'inconclusive' if unresolved or not rows else
        'observed_pass_with_execution_debt' if execution_debt else 'observed_pass')
    reasons={}
    for row in rows:
        if row.status in {'passed','failed'}:
            continue
        key=(row.status,tuple(row.reasons),row.target.status,row.candidate.status,
            row.target.error,row.candidate.error)
        if key not in reasons:
            reasons[key]={'comparison_status':row.status,'reasons':list(row.reasons),
                'target_status':row.target.status,'candidate_status':row.candidate.status,
                'target_error':row.target.error,'candidate_error':row.candidate.error,
                'count':0,'example_case':row.case}
        reasons[key]['count']+=1
    groups=sorted(reasons.values(),key=lambda r:(-r['count'],r['example_case']))
    return {'status':status,'counts':counts,'observed_disagreements':failed,
        'inconclusive_comparisons':unresolved,'inconclusive_reason_groups':groups[:8],
        'omitted_reason_groups':max(0,len(groups)-8),
        'omitted_inconclusive_cases':sum(group['count'] for group in groups[8:]),
        'scope':'comparison outcomes, not universal semantics or proof of fault ownership'}


class DeferredPanel:
    """Prepare target-led tests only once a frontend-passing child exists.

    A failed root need not have generated normalized object dumps yet. Do not
    permanently disable semantics for children that create those artifacts.
    Retry failed initialization when target artifacts or includes change; a successful
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
                        for name in ('target.o','target_object_dump_normalized.s','.compiler-target.json'))
            includes = '\n'.join(re.findall(r'(?m)^[ \t]*#[ \t]*include[^\n]+',state.source))
            key += (hashlib.sha256(includes.encode()).hexdigest(),)
            if self.failed_key != key:
                try:
                    panel = Panel(*self.args, 5000, None, state.source)
                    if panel is None:
                        raise ValueError('semantic panel factory returned no evaluator')
                    self.panel = panel
                except (OSError,ValueError) as exc:
                    self.failed_key = key
                    self.failure = {'status':'unavailable','reason':str(exc),'authoritative':False}
                    if getattr(exc, 'evidence', None):
                        self.failure['environment_obligations'] = exc.evidence
            if self.panel is None:
                return {**self.failure,'source_sha256':hashlib.sha256(state.source.encode()).hexdigest()}
        return self.panel(state)


class Panel:
    def __init__(self, repo, ws, function, max_cases=64, max_steps=10000, exploration_cases=5000,
                 callee_environment=None, header_source=None):
        from eval import dag_pipeline_pilot as dag
        from solver import callee_execution
        if min(max_cases,max_steps,exploration_cases) <= 0:
            raise ValueError('semantic budgets must be positive')
        self.ws, self.function, self.max_steps = ws, function, max_steps
        self.cache = {}
        self.target = workspace.semantic_assembly((ws/'target_object_dump_normalized.s').read_text(),ws/'target.o')
        from solver import hardware_environment
        hardware = hardware_environment.obligations(repo,self.target)
        if hardware:
            raise hardware_environment.Required(hardware)
        abi_context = None
        if header_source is not None and (ws/'.compiler-target.json').is_file():
            target_config = json.loads((ws/'.compiler-target.json').read_text())
            if target_config.get('function') != function:
                raise ValueError('semantic ABI compiler target belongs to another function')
            abi_context = dict(source=header_source,target=target_config['target'],ws=ws)
        self.abi = (dag.prototype_info(repo,function,self.target,abi_context=abi_context)
                    if abi_context else dag.prototype_info(repo,function,self.target))
        calls = project_headers.called_functions(self.target)
        self.arities, contracts = (dag._call_contracts(repo,calls,abi_context) if abi_context
                                  else dag._call_contracts(repo,calls))
        self.call_contracts = contracts
        if callee_environment is None:
            self.callee_environment, callee_report = callee_execution.load_binary_leaves(repo,calls,contracts)
        else:
            self.callee_environment, callee_report = callee_environment, []
        environment_manifest = self.callee_environment.manifest()
        for name, leaf in self.callee_environment.leaves.items():
            if leaf.word_pair_multiply:
                self.arities[name] = 4
                contracts[name] = {'arity_known':True, 'arity_source':'ROM-bound word-pair instruction dialect',
                                   'return_registers':['v0','v1']}
        self.debt = ['finite synthetic inputs; not universal equivalence']
        extent_report = {'status':'unavailable','measurements':[],'declines':[]}
        if abi_context:
            from solver import type_constraints
            linked = differential.Program.parse(function,self.target).symbol_addresses
            provided = set(project_headers._included_declarations(repo,header_source))
            names = sorted(set(linked) & provided)
            extent_report['omitted'] = names[8:]
            for name in names[:8]:
                try:
                    measured = type_constraints.measure(repo,ws,header_source,function,
                        abi_context['target'],global_symbols=(name,))
                    extent = measured['global_extents'][0]
                    if not 0 < extent['size'] <= 8*1024*1024:
                        raise ValueError('header object outside memory resource bound')
                    self.target += '\n# MIPS_DIFF_EXTENT '+name+' '+str(extent['size'])+'\n'
                    extent_report['measurements'].append({'extent':extent,
                        'receipt':measured['receipt_path'],'probe_object_sha256':measured['probe_object_sha256']})
                except (OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
                    extent_report['declines'].append({'symbol':name,'reason':str(exc)})
            extent_report['status']='measured' if extent_report['measurements'] else 'unavailable'
        if extent_report['measurements']:
            self.debt.append('global extents use target-compiler-measured project headers; synthetic contents, not binary-only environment')
        opaque = [n for n in calls if n not in self.callee_environment.leaves]
        if opaque: self.debt.append('opaque callees: '+', '.join(opaque))
        if self.callee_environment.leaves:
            self.debt.append('bounded integer callee execution; frame bounds do not prove C object extents; nested calls unsupported')
        if self.callee_environment.outputs:
            self.debt.append('explicit output-buffer environment assumptions, not hardware emulation')
        if any(i.opcode=='jalr' for i in differential.Program.parse(function,self.target).instructions):
            self.debt.append('indirect callees lack concrete implementation/arity contracts')
        self.debt.extend(self.abi.get('issues',[]))
        self.debt.extend('unknown call arity: '+name for name,value in contracts.items() if not value['arity_known'])
        self.debt.extend(issue for value in contracts.values() for issue in value.get('issues',[]))
        self.returns = tuple(self.abi['return_registers'])
        arguments = dict(call_arities=self.arities,return_registers=self.returns,
            mutable_entry_registers=tuple(self.abi['mutable_scalar_registers']),
            pointer_entry_registers=tuple(n for n in self.abi.get('pointer_registers',[]) if n!='a0'),
            max_steps=max_steps,callee_environment=self.callee_environment)
        original_seeds=dag._seed_cases(function,self.abi)
        explored = differential.explore_coverage(self.target,original_seeds,
            target_name=function,max_cases=exploration_cases,max_total_steps=max_steps*16,**arguments)
        budget_phases=[{'per_case_steps':max_steps,'exploration':explored.to_dict()}]
        # Reallocate work, don't multiply thousands of near-identical timed-out
        # inputs. Only restart from original/retained target inputs, never a
        # candidate-selected state or a resynchronized execution checkpoint.
        if (not any(run.status in differential.COMPLETED_STATUSES for run in explored.runs)
                and explored.trial_status_counts.get('step_limit') and max_steps < 1_000_000):
            retry_seeds=tuple(case for case,run in zip(explored.cases,explored.runs)
                              if run.status=='step_limit')[:8] or original_seeds
            retry_steps=min(max_steps*16,1_000_000)
            remaining=max_steps*128-explored.executed_steps
            arguments['max_steps']=retry_steps
            self.max_steps=max_steps=retry_steps
            explored=differential.explore_coverage(self.target,retry_seeds,
                target_name=function,max_cases=min(exploration_cases,32),
                max_total_steps=remaining,**arguments)
            budget_phases.append({'per_case_steps':retry_steps,'exploration':explored.to_dict()})
            self.debt.append('bounded higher-step target retry; unresolved paths and earlier noncompleted trials remain debt')
        self.execution_obstructions = explored.execution_obstructions
        noncompleted = [(case, run) for case, run in zip(explored.cases, explored.runs)
                        if run.status not in differential.COMPLETED_STATUSES]
        examples = {}
        for case, run in noncompleted:
            shape = (run.status, run.error)
            if shape not in examples and len(examples) < 8:
                examples[shape] = {'case': asdict(case), 'status': run.status,
                    'error': run.error, 'instruction_count': run.instruction_count}
        self.target_execution = {
            'status_counts': dict(Counter(run.status for run in explored.runs)),
            'noncompleted_count': len(noncompleted), 'examples': list(examples.values()),
            'example_limit': 8,
            'authority': 'bounded target exploration; noncompletion is not proof of nontermination'}
        if self.execution_obstructions:
            self.debt.append('target exploration encountered unsupported execution; passing selected cases do not discharge it')
        seeds = tuple(case for case,run in zip(explored.cases,explored.runs)
                      if run.status in differential.COMPLETED_STATUSES)
        self.cases = ()
        stress_report = {'status':'not_run','reason':'no completed target exploration seeds'}
        self.stress_execution_debt = False
        if seeds:
            stress = differential.build_semantic_stress_panel(self.target,seeds,
                target_name=function,max_cases=max(max_cases,len(seeds)),
                max_trials=max(max_cases*4,len(seeds)),
                max_total_steps=budget_phases[0]['per_case_steps']*128,
                max_generated=max(1024,max_cases*64),**arguments)
            self.cases = stress.cases
            stress_report = {'status':'bounded_diagnostic', **stress.to_dict()}
            self.stress_execution_debt = bool(stress.stop_reasons or stress.rejected_status_counts
                                              or stress.unattempted_seed_count)
            if self.stress_execution_debt:
                self.debt.append('stress exploration rejected or did not enumerate/execute some target inputs; see stress_work receipt')
        self.identity = hashlib.sha256(json.dumps({'target':self.target,'cases':[asdict(c) for c in self.cases],
            'abi':self.abi,'arities':self.arities,'steps':max_steps,
            'call_contracts':self.call_contracts,
            'comparison_policy':'unknown-direct-argument-only-inconclusive-v1',
            'exploration_budget_phases':budget_phases,
            'stress_work':stress_report,
            'callee_environment':environment_manifest,
            'runner':hashlib.sha256(Path(differential.__file__).read_bytes()).hexdigest()},sort_keys=True).encode()).hexdigest()
        self.report = {'panel_sha256':self.identity,'abi':self.abi,'call_arities':self.arities,
            'comparison_policy':'unknown-direct-argument-only-inconclusive-v1',
            'stress_work':stress_report,
            'exploration_budget_phases':budget_phases,
            'global_extent_admission':extent_report,
            'call_contracts':self.call_contracts,
            'callee_environment':environment_manifest,'callee_admission':callee_report,
            'cases':[asdict(c) for c in self.cases], 'target_exploration':explored.report.to_dict(),
            'execution_obstructions':list(self.execution_obstructions),
            'target_execution':self.target_execution,
            'exploration_trials':{'attempted_cases':explored.attempted_cases,
                'stop_reason':explored.stop_reason,'status_counts':explored.trial_status_counts,
                'noncompleted_examples':list(explored.noncompleted_trial_examples),
                'scope':'all exploration trials, including discarded prefixes; not the selected semantic panel'},
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
                    'reason':'no completed target cases','debt':self.debt,'authoritative':False,
                    'target_execution':self.target_execution,
                    'exploration_trials':self.report.get('exploration_trials'),
                    'exploration_budget_phases':self.report['exploration_budget_phases'],
                    'stress_work':self.report['stress_work'],
                    'target_coverage':self.report['target_exploration'],
                    'execution_obstructions':list(self.execution_obstructions),
                    'call_contracts':self.call_contracts,
                    'callee_environment':self.report['callee_environment'],
                    'callee_admission':self.report['callee_admission']}
        obj = state.object_path
        assembly = workspace.semantic_assembly(obj.with_name(obj.stem+'_object_dump_normalized.s').read_text(),obj)
        rows = differential.run_suite(self.target,assembly,self.cases,target_name=self.function,
            candidate_name=self.function+'-candidate',call_arities=self.arities,
            return_registers=self.returns,max_steps=self.max_steps,callee_environment=self.callee_environment)
        raw_counts = dict(Counter(row.status for row in rows))
        unknown_argument_evidence = []
        classified = []
        for row in rows:
            revised = classify_unknown_direct_arguments(row, self.call_contracts)
            if revised is not row and len(unknown_argument_evidence) < 4:
                unknown_argument_evidence.append({'case': row.case, 'raw_status': row.status,
                    'raw_reasons': row.reasons, 'first_divergence': row.first_divergence,
                    'status': revised.status, 'reason': revised.reasons[0],
                    'next_action': 'resolve callee ABI from independent header/binary evidence; do not repair guessed unused registers'})
            classified.append(revised)
        rows = classified
        counts = dict(Counter(row.status for row in rows))
        obligations = {}
        for row in rows:
            for obligation in opaque_stack_obligations(row):
                identity = json.dumps(obligation, sort_keys=True)
                if identity not in obligations:
                    obligations[identity] = {**obligation, 'case_count': 0, 'example_case': row.case}
                obligations[identity]['case_count'] += 1
        debt = list(self.debt)
        indirect = {}
        for row in rows:
            for obligation in indirect_call_obligations(row):
                identity = (obligation['call_ordinal'], obligation['target_callee'],
                            tuple(word['word'] for word in obligation['unclassified_abi_word_differences']))
                if identity not in indirect and len(indirect) < 16:
                    indirect[identity] = {**obligation,'example_case':row.case}
        if indirect:
            debt.append('indirect call arguments/effects are unmodeled; ABI-window differences are diagnostic only')
        if obligations:
            debt.append('opaque calls expose stack addresses without pointee contents/effects; '
                        'address disagreement is not itself a proven C bug and equal addresses do not prove equal inputs')
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
            stack_obligations = opaque_stack_obligations(row)
            if stack_obligations:
                packet['opaque_stack_obligations'] = stack_obligations[:8]
                packet['omitted_stack_obligations'] = max(0, len(stack_obligations)-8)
            callback_contracts = {
                side:[{'call_ordinal':call.ordinal,'callee':call.callee,
                       'contract':call.abi_contract}
                      for call in run.calls if call.abi_contract][:4]
                for side,run in [('target',row.target),('candidate',row.candidate)]}
            if any(callback_contracts.values()):
                packet['callback_argument_contracts'] = callback_contracts
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
        outcomes=outcome_accounting(rows,execution_debt=bool(self.execution_obstructions or obligations or indirect
                                                          or self.stress_execution_debt))
        from solver import source_object_bounds
        object_obligations=source_object_bounds.obligations(state.source,self.function)
        result = {'status':outcomes['status'],'outcome_accounting':outcomes,
            'raw_comparison_counts':raw_counts,
            'unknown_direct_argument_evidence':unknown_argument_evidence,
            'stress_work':self.report['stress_work'],
            'source_object_obligations':object_obligations,
            'source_sha256':key,'panel_sha256':self.identity,'counts':counts,'total':len(rows),
            'semantic_key':list(repair.behavior_key(rows,state.attempt)[:-2]),
            'passed_case_indices':[i for i,row in enumerate(rows) if row.status=='passed'],
            'authoritative':False,'debt':debt,
            'call_contracts':self.call_contracts,
            'opaque_stack_obligations':list(obligations.values()),
            'indirect_call_obligations':list(indirect.values()),
            'execution_obstructions':list(self.execution_obstructions),
            'callee_environment':self.callee_environment.manifest(),
            'exploration_trials':self.report.get('exploration_trials'),
            'callee_source_contracts':callee_execution.source_contracts(state.source,self.callee_environment),
            'target_coverage':differential.coverage_report(differential.Program.parse(self.function,self.target),[r.target for r in rows]).to_dict(),
            'candidate_coverage':differential.coverage_report(differential.Program.parse(self.function,assembly),[r.candidate for r in rows]).to_dict(),
            'operation_gradient':semantic_gradient.render_operation_gradient(rows,state.source,max_clusters=3),
            'feedback':feedback}
        self.cache[key] = result
        obj.with_suffix('.semantic-lane.json').write_text(json.dumps(result,indent=2)+'\n')
        return result
