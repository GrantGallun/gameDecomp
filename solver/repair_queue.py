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
    unit: str | None = None
    dependency_depth: int = 0


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
    if semantic.get('status') in {'unavailable', 'inconclusive'}:
        # Unresolved execution/ABI comparisons supply no established source
        # counterexample. Keep deterministic exact search available, but do not
        # send these to ordinary byte-polish model profiles by default.
        return Lane.ENVIRONMENT
    if not semantic and residual.get('compiled') and (residual.get('frontend') or {}).get('passed') is True:
        return Lane.VALIDATE
    return Lane.BYTE


def evidence_key(node):
    """Exclude visit counters/scores/timestamps: logging is not new evidence."""
    semantic = node.get('semantic_validation') or {}
    frontend = (node.get('residual') or {}).get('frontend') or {}
    phase = lane(node)
    # Inconclusive receipts historically used the byte lane. A routing fix is
    # not new measured evidence: retain those keys so already attempted search
    # profiles do not regain their budgets merely because the policy changed.
    key_lane = Lane.BYTE if phase == Lane.ENVIRONMENT and semantic.get('status') == 'inconclusive' else phase
    return fingerprint({
        'source': node.get('source_sha256'), 'lane': key_lane.value,
        **({'shared_evidence': node['shared_evidence_sha256']} if node.get('shared_evidence_sha256') else {}),
        **({'binary_data': node['data_evidence_sha256']} if node.get('data_evidence_sha256') else {}),
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
            semantic = node.get('semantic_validation') or {}
            identity = {'reason': semantic.get('reason')}
            if semantic.get('status') == 'inconclusive':
                accounting = semantic.get('outcome_accounting') or {}
                # Case names and frequencies describe coverage, not distinct
                # missing capabilities. Preserve the exact observed reason and
                # execution states, without guessing a shared root cause.
                signatures = {
                    fingerprint(signature): signature
                    for row in accounting.get('inconclusive_reason_groups', [])
                    if (signature := {k: row.get(k) for k in (
                        'comparison_status', 'reasons', 'target_status',
                        'candidate_status', 'target_error', 'candidate_error')})
                    and (signature.get('reasons') or signature.get('target_error')
                         or signature.get('candidate_error'))
                }
                identity = {'comparison_status': 'inconclusive',
                            'obstructions': [signatures[k] for k in sorted(signatures)]}
                if not signatures or accounting.get('omitted_reason_groups'):
                    identity['function'] = name
            # Unknown causes must not be falsely merged into one shared fix.
            elif not identity['reason']:
                identity['function'] = name
        else:
            identity = {'function': name, 'blocker': blocker}
        key = fingerprint({'kind': kind, 'identity': identity})
        grouped.setdefault(key, (kind, identity, []))[2].append(name)
    return {key: asdict(SharedIssue(key, kind, identity, tuple(sorted(names))))
            for key, (kind, identity, names) in sorted(grouped.items())}


def remaining_by_unit(eligible, tu_index):
    """Runnable functions per layout cluster; exhausted work is not runnable."""
    remaining = {}
    for name in eligible:
        unit = tu_index.get(name)
        if unit is None:
            continue
        remaining[unit] = remaining.get(unit, 0) + 1
    return remaining


def dependency_depths(eligible, dependencies):
    """Callee-first SCC depths over runnable work, without a dependency gate.

    Completed, parked and exhausted callees cannot be advanced by this queue.
    Cycles share a depth; no recursive traversal or per-node component copies.
    """
    eligible = set(eligible)
    graph = {n: set(dependencies.get(n, ())) & eligible for n in eligible}
    depths = {}
    for group in components(graph):
        members = set(group)
        external = {c for n in group for c in graph[n] if c not in members}
        depth = max((depths[c] + 1 for c in external), default=0)
        depths.update((n, depth) for n in group)
    return depths


def project(state, legacy_profiles):
    """O(V + E + J) scan plus O(V) heapify, apart from canonical hashing/sorting.

    Fair two-visit bands prevent lane priority starving expensive functions.
    Leverage is a scheduling heuristic, never a proof of an interface or type.

    With a `tu_index`, runnable callees precede callers within fair visit/lane
    bands, then caller leverage and near-finished layout clusters break ties.
    Equal-sized clusters stay together by address. These are scheduling hints,
    not proven original files or interface contracts. Cycles remain runnable.
    Unknown clusters sort last in the locality term and are reported. An
    absent index preserves the previous ordering for older frozen campaigns.
    """
    items, profiles = [], {}
    nodes = state['nodes']
    issues = shared_issues(nodes)
    callers = state.get('dependency_graph', {}).get('callers', {})
    order = {Lane.INTAKE: 0, Lane.FRONTEND: 1, Lane.VALIDATE: 2,
             Lane.SEMANTIC: 3, Lane.BYTE: 4, Lane.ENVIRONMENT: 5}
    tu_index = state.get('tu_index') or {}
    phases = {name: lane(node) for name, node in nodes.items()}
    for name, node in nodes.items():
        if state['config'].get('compile_sweep') and phases[name] not in {Lane.INTAKE, Lane.FRONTEND}:
            continue
        if state['config'].get('scheduler') == 'investigation-v1':
            from solver.investigation import profile as investigation_profile
            profile = investigation_profile(node, state['config']['model_calls'], legacy_profiles)
        else:
            profile = next_profile(node, state['config']['model_calls'], legacy_profiles)
        if profile is not None:
            profiles[name] = profile
    remaining = remaining_by_unit(profiles, tu_index) if tu_index else {}
    depths = dependency_depths(profiles, state.get('dependency_graph', {}).get('callees', {})) if tu_index else {}
    # Rank clusters by their earliest known binary address, with deterministic
    # labels for incomplete metadata. This prevents equal-size unit interleaving.
    unit_addresses = {}
    for name, unit in tu_index.items():
        address = nodes.get(name, {}).get('address')
        if unit is not None and isinstance(address, int) and address >= 0:
            unit_addresses[unit] = min(address, unit_addresses.get(unit, address))
    units = sorted(remaining, key=lambda u: (u not in unit_addresses, unit_addresses.get(u, 0), u))
    unit_rank = {unit: i for i, unit in enumerate(units)}
    # Unknown unit must never outrank a real near-finished one.
    unknown_unit = len(nodes) + 1
    unindexed = []
    for name, profile in profiles.items():
        node = nodes[name]
        phase = phases[name]
        leverage = sum(c in profiles for c in callers.get(name, [])) if tu_index else sum(
            phases[c] != Lane.DONE for c in callers.get(name, []) if c in nodes)
        if not tu_index:
            locality = 0
        elif tu_index.get(name) is not None:
            locality = remaining.get(tu_index[name], unknown_unit)
        else:
            locality = unknown_unit
            unindexed.append(name)
        depth = depths.get(name, 0)
        priority = (len(node.get('jobs', [])) // 2, order[phase], depth, -leverage, locality,
                    unit_rank.get(tu_index.get(name), unknown_unit) if tu_index else 0,
                    node.get('instruction_count') or 0, name)
        item = WorkItem(name, phase.value, profile['name'], profile['evidence_key'], priority,
                        tu_index.get(name), depth)
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
            priority = (len(nodes[name].get('jobs', [])) // 2, -1, 0, -len(issue['affected_functions']), 0, 0, 0, name)
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
                'ordering': 'callee-cluster-v2' if tu_index else 'legacy-evidence',
                'priority_fields': ['visit_band', 'lane', 'dependency_depth', 'negative_callers',
                                    'runnable_in_unit', 'unit_address_rank', 'instructions', 'function'],
                'work_items': {item.function: asdict(item) for item in items},
                'shared_issues': issues,
                'locality': {'indexed': bool(tu_index), 'units_with_work': len(remaining),
                             'unindexed_functions': sorted(unindexed)}}
    return snapshot, ((selected, profiles[selected]) if selected else None)
