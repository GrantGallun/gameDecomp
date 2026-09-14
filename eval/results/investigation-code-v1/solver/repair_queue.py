"""Evidence-directed scheduling projections over authoritative campaign receipts.

No compiler/runtime verdicts are produced here. Missing capabilities remain
visible issues, not executable source-edit jobs. JSON checkpoints own durability;
the heap and reverse adjacency are reconstructed, never independently persisted.
"""
from dataclasses import asdict, dataclass
from enum import Enum
import heapq

from solver.evidence_schedule import components, fingerprint


class Lane(str, Enum):
    INTAKE = "intake"
    FRONTEND = "frontend"
    SEMANTIC = "semantic"
    VALIDATE = "validate"
    BYTE = "byte"
    ENVIRONMENT = "environment"
    BLOCKED = "blocked"
    DONE = "done"


@dataclass(frozen=True)
class WorkItem:
    function: str
    lane: str
    profile: str
    evidence_key: str
    priority: tuple


@dataclass(frozen=True)
class SharedIssue:
    key: str
    kind: str
    identity: dict
    affected_functions: tuple
    executable: bool = False


def lane(node):
    if node['status'] in {'object_exact', 'integrated', 'function_exact_pending_integration'}:
        return Lane.DONE
    if node['status'] == 'parked':
        return Lane.BLOCKED
    if not node.get('source_sha256'):
        return Lane.INTAKE
    residual = node.get('residual') or {}
    if residual.get('compiled') is False or (residual.get('frontend') or {}).get('passed') is False:
        return Lane.FRONTEND
    semantic = node.get('semantic_validation') or {}
    if semantic.get('source_sha256') not in {None, node.get('source_sha256')}:
        return Lane.VALIDATE
    if semantic.get('status') == 'observed_failure':
        return Lane.SEMANTIC
    if semantic.get('status') == 'unavailable':
        return Lane.ENVIRONMENT
    if not semantic and residual.get('compiled') and (residual.get('frontend') or {}).get('passed') is True:
        return Lane.VALIDATE
    return Lane.BYTE


def evidence_key(node):
    """Exclude visit counters/scores/timestamps: logging is not new evidence."""
    semantic = node.get('semantic_validation') or {}
    frontend = (node.get('residual') or {}).get('frontend') or {}
    return fingerprint({
        'source': node.get('source_sha256'), 'lane': lane(node).value,
        **({'shared_evidence': node['shared_evidence_sha256']} if node.get('shared_evidence_sha256') else {}),
        'compiler': {k: (node.get('residual') or {}).get(k)
                     for k in ('compiled', 'compiler_error_signature')},
        'frontend': {k: frontend.get(k) for k in ('passed', 'status', 'diagnostics')},
        'semantic': {k: semantic.get(k) for k in (
            'status', 'source_sha256', 'panel_sha256', 'semantic_key', 'counts', 'reason', 'feedback')},
        'blocker': node.get('blocker'),
    })


def next_profile(node, model_calls, legacy_profiles):
    phase = lane(node)
    if phase in {Lane.DONE, Lane.BLOCKED}:
        return None
    key = evidence_key(node)
    used = {j['profile'] for j in node.get('jobs', [])
            if j.get('evidence_key') == key}
    if phase == Lane.INTAKE:
        profiles = [{'name': 'intake', 'model': False}]
    elif phase == Lane.VALIDATE:
        profiles = [{'name': 'semantic_validate', 'model': False, 'deterministic_budget': 0,
                     'brief': 'Measure source-bound differential behavior before choosing a repair lane.'}]
    elif phase == Lane.SEMANTIC:
        profiles = [
            {'name': 'semantic_counterexample', 'model': True, 'deterministic_budget': 0,
             'brief': 'Repair the first causal divergence and its dependent values using the supplied '
                      'differential counterexamples. Preserve passing cases; byte score is secondary.'},
            {'name': 'semantic_alternative', 'model': True, 'think': 'high', 'deterministic_budget': 0,
             'brief': 'Test a different causal explanation of the differential failure: address units, '
                      'signedness, branch predicate, call effects or lifetime. Use measured evidence.'},
        ]
    elif phase == Lane.FRONTEND:
        profiles = [{'name': 'compile_recovery', 'model': False, 'deterministic_budget': 0}]
        profiles += [p for p in legacy_profiles if p['model']]
        # Source changes alone must not allow endless header-only recovery.
        tail = node.get('jobs', [])[-2:]
        if len(tail) == 2 and all(j['profile'] == 'compile_recovery' for j in tail):
            profiles = profiles[1:]
    else:
        profiles = [p for p in legacy_profiles if not p.get('type_transaction')]
        if phase == Lane.ENVIRONMENT:
            # Exact-object search can still succeed without runtime execution.
            # Do not spend model calls pretending there is a counterexample.
            profiles = [p for p in profiles if not p['model']]
    for profile in profiles:
        if profile['name'] not in used and (model_calls or not profile['model']):
            return {**profile, 'lane': phase.value, 'evidence_key': key}
    return None


def graph(dependencies, selected):
    """Sparse directed graph plus SCC membership; no hard callee gate."""
    selected = set(selected)
    outgoing = {n: set(dependencies.get(n, ())) & selected for n in selected}
    incoming = {n: set() for n in outgoing}
    for caller, callees in outgoing.items():
        for callee in callees:
            incoming[callee].add(caller)
    return {'callees': {n: sorted(v) for n, v in outgoing.items()},
            'callers': {n: sorted(v) for n, v in incoming.items()},
            'components': sorted(tuple(group) for group in components(outgoing))}


def shared_issues(nodes):
    grouped = {}
    for name, node in nodes.items():
        phase = lane(node)
        if phase not in {Lane.BLOCKED, Lane.ENVIRONMENT}:
            continue
        blocker = node.get('blocker') or {}
        evidence = blocker.get('evidence') or {}
        kind = blocker.get('status', 'execution_environment')
        if kind == 'object_postprocessing_backend_required':
            identity = {k: evidence.get(k) for k in ('target', 'postprocess', 'makefile_sha256', 'projection_sha256')}
        elif phase == Lane.ENVIRONMENT:
            identity = {'reason': (node.get('semantic_validation') or {}).get('reason')}
            # Unknown causes must not be falsely merged into one shared fix.
            if not identity['reason']:
                identity['function'] = name
        else:
            identity = {'function': name, 'blocker': blocker}
        key = fingerprint({'kind': kind, 'identity': identity})
        grouped.setdefault(key, (kind, identity, []))[2].append(name)
    return {key: asdict(SharedIssue(key, kind, identity, tuple(sorted(names))))
            for key, (kind, identity, names) in sorted(grouped.items())}


def project(state, legacy_profiles):
    """O(V + E + J) scan plus O(V) heapify, apart from canonical hashing/sorting.

    Fair two-visit bands prevent lane priority starving expensive functions.
    Leverage is a scheduling heuristic, never a proof of an interface or type.
    """
    items, profiles = [], {}
    nodes = state['nodes']
    issues = shared_issues(nodes)
    callers = state.get('dependency_graph', {}).get('callers', {})
    order = {Lane.INTAKE: 0, Lane.FRONTEND: 1, Lane.VALIDATE: 2,
             Lane.SEMANTIC: 3, Lane.BYTE: 4, Lane.ENVIRONMENT: 5}
    for name, node in nodes.items():
        phase = lane(node)
        if state['config'].get('compile_sweep') and phase not in {Lane.INTAKE, Lane.FRONTEND}:
            continue
        if state['config'].get('scheduler') == 'investigation-v1':
            from solver.investigation import profile as investigation_profile
            profile = investigation_profile(node, state['config']['model_calls'], legacy_profiles)
        else:
            profile = next_profile(node, state['config']['model_calls'], legacy_profiles)
        if profile is None:
            continue
        leverage = sum(lane(nodes[c]) != Lane.DONE for c in callers.get(name, []) if c in nodes)
        priority = (len(node.get('jobs', [])) // 2, order[phase], -leverage,
                    node.get('instruction_count') or 0, name)
        item = WorkItem(name, phase.value, profile['name'], profile['evidence_key'], priority)
        items.append(item)
        profiles[name] = profile
    heap = [(item.priority, item.function) for item in items]
    if state['config'].get('scheduler') == 'investigation-v1':
        for key, issue in issues.items():
            task = state['config'].get('capability_tasks', {}).get(key)
            if not task or task.get('evidence') != issue['identity']:
                continue
            task_key = fingerprint(task)
            if any(j.get('evidence_key') == task_key and j.get('profile') == 'capability_repair'
                   for n in nodes.values() for j in n.get('jobs', [])):
                continue
            name = min(issue['affected_functions'], key=lambda n: (len(nodes[n].get('jobs', [])), n))
            priority = (len(nodes[name].get('jobs', [])) // 2, -1, -len(issue['affected_functions']), 0, name)
            item = WorkItem(name, 'capability', 'capability_repair', task_key, priority)
            # One executable item per function per selection. The ordinary
            # source work remains eligible after this bounded shared experiment.
            items = [old for old in items if old.function != name]
            items.append(item)
            profiles[name] = {'name': 'capability_repair', 'model': bool(state['config']['model_calls']),
                             'capability_task': task, 'issue_key': key, 'lane': 'capability',
                             'evidence_key': task_key}
            issue.update(executable=True, action='isolated engineering experiment', task_sha256=task_key)
        heap = [(item.priority, item.function) for item in items]
    heapq.heapify(heap)
    selected = heapq.heappop(heap)[1] if heap else None
    snapshot = {'schema_version': 1, 'policy': state['config'].get('scheduler', 'evidence-v1'),
                'work_items': {item.function: asdict(item) for item in items},
                'shared_issues': issues}
    return snapshot, ((selected, profiles[selected]) if selected else None)
