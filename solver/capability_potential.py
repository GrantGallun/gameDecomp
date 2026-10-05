"""Bounded contract reachability. Predicted states are never compiler evidence."""
from copy import deepcopy

from eval.search_replay import digest


def _facts(value, *, unknown=False):
    allowed = (bool, type(None)) if unknown else (bool,)
    if not isinstance(value, dict) or any(
        not isinstance(k, str) or not k or type(v) not in allowed for k, v in value.items()
    ):
        raise ValueError('facts must map nonempty predicate names to booleans' + (' or unknown' if unknown else ''))


def _names(value):
    if not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value) or len(set(value)) != len(value):
        raise ValueError('preserves/invalidates must be lists of distinct predicate names')


def _validate_inputs(facts, operations, goals, max_depth, max_nodes):
    _facts(facts, unknown=True)
    if type(max_depth) is not int or not 0 <= max_depth <= 64:
        raise ValueError('max_depth must be an integer between 0 and 64')
    if type(max_nodes) is not int or not 1 <= max_nodes <= 10000:
        raise ValueError('max_nodes must be an integer between 1 and 10000')
    if not isinstance(operations, list) or not isinstance(goals, dict):
        raise ValueError('operations must be a list and goals a dictionary')
    ids = set()
    for op in operations:
        if not isinstance(op, dict) or not isinstance(op.get('id'), str) or not op['id'] or op['id'] in ids:
            raise ValueError('operations require distinct nonempty ids')
        ids.add(op['id'])
        _facts(op.get('requires')); _facts(op.get('produces'))
        _names(op.get('preserves')); _names(op.get('invalidates'))
        p, k, d = set(op['produces']), set(op['preserves']), set(op['invalidates'])
        if p & k or p & d or k & d:
            raise ValueError('produces, preserves and invalidates must be disjoint')
    for name, requirements in goals.items():
        if not isinstance(name, str) or not name or not requirements:
            raise ValueError('goals require a nonempty name and conjunction')
        _facts(requirements)


def generate(facts, operations, goals, *, context=None, max_depth=3, max_nodes=256):
    """Compose instantiated Boolean contracts, keeping unknown assumptions local.

    A false precondition blocks a transition. An unknown precondition permits a
    conditional transition and remains a recorded obligation. Only explicit
    preserves/produces survive a transition; other knowledge is forgotten.
    Alternatives are distinct operations and never merge their states.
    """
    _validate_inputs(facts, operations, goals, max_depth, max_nodes)
    if context is not None and not isinstance(context, dict):
        raise ValueError('context must be a dictionary')
    inputs = deepcopy(dict(facts=facts, operations=operations, goals=goals,
                           context=context, max_depth=max_depth, max_nodes=max_nodes))
    names = set(facts)
    for op in operations:
        names.update(op['requires']); names.update(op['produces'])
        names.update(op['preserves']); names.update(op['invalidates'])
    for requirements in goals.values():
        names.update(requirements)
    names = sorted(names)
    operations = sorted(operations, key=lambda op: op['id'])

    def gap(predicate, required, actual):
        return {'predicate': predicate, 'required': required, 'actual': actual,
                'producers': [op['id'] for op in operations if op['produces'].get(predicate) is required]}

    def signature(node):
        return digest([node['facts'], node['assumed']])

    def satisfies(node, requirements):
        # Requiring an unknown goal as an input cannot manufacture its achievement.
        return all(node['facts'][p] is v and p not in node['assumed'] for p, v in requirements.items())

    root = {'id': 'root', 'parent': None, 'via': [], 'facts': {p: facts.get(p) for p in names},
            'conditions': [], 'assumed': []}
    nodes = [root]
    ancestry = {'root': {signature(root)}}
    blocked, paths = [], []
    potential = {op['id']: [] for op in operations}
    limits = {'max_depth': max_depth, 'max_nodes': max_nodes, 'depth_cutoff': False, 'node_cutoff': False}

    def record_goals(node):
        for name, requirements in sorted(goals.items()):
            if not satisfies(node, requirements):
                continue
            conditions = deepcopy(node['conditions'])
            path = {'goal': name, 'node': node['id'], 'via': list(node['via']),
                    'conditions': conditions, 'gaps': [],
                    'status': 'conditional' if conditions else 'supported-by-contracts',
                    'evidence_status': 'contract-inference' if node['via'] else 'supplied-input',
                    'next_test': {'action': 'establish-prerequisite', **conditions[0]} if conditions else
                                 {'action': 'execute-and-validate', 'operation': node['via'][0]} if node['via'] else
                                 {'action': 'retain-input-evidence'}}
            paths.append(path)
            if node['via']:
                potential[node['via'][0]].append(deepcopy(path))

    record_goals(root)
    for node in nodes:  # Breadth first, with a hard bound on appended nodes.
        for op in operations:
            failed = [gap(p, v, node['facts'][p]) for p, v in sorted(op['requires'].items())
                      if node['facts'][p] is not None and node['facts'][p] is not v]
            if failed:
                blocked.append({'node': node['id'], 'operation': op['id'], 'gaps': failed})
                continue
            assumed_facts = dict(node['facts'])
            assumed = set(node['assumed'])
            conditions = deepcopy(node['conditions'])
            for p, v in sorted(op['requires'].items()):
                if assumed_facts[p] is None:
                    assumed_facts[p] = v
                    assumed.add(p)
                    conditions.append({'predicate': p, 'value': v, 'step': len(node['via']),
                                       'operation': op['id'], 'evidence_status': 'unestablished'})
            child_facts = {p: assumed_facts[p] if p in op['preserves'] else None for p in names}
            child_facts.update(op['produces'])
            # Reasserting the very assumption required by this operation does
            # not turn it into an established output. An independent producer
            # that does not require that predicate can establish it instead.
            retained_assumptions = assumed & set(op['preserves'])
            retained_assumptions.update(p for p, v in op['produces'].items()
                                        if p in assumed and op['requires'].get(p) is v)
            child = {'id': f'n{len(nodes)}', 'parent': node['id'], 'via': node['via'] + [op['id']],
                     'facts': child_facts, 'conditions': conditions,
                     'assumed': sorted(retained_assumptions)}
            sig = signature(child)
            if sig in ancestry[node['id']]:
                continue
            if len(node['via']) >= max_depth:
                limits['depth_cutoff'] = True
                continue
            if len(nodes) >= max_nodes:
                limits['node_cutoff'] = True
                continue
            nodes.append(child)
            ancestry[child['id']] = ancestry[node['id']] | {sig}
            record_goals(child)

    goal_summary = {}
    for name, requirements in sorted(goals.items()):
        found = [p for p in paths if p['goal'] == name]
        goal_summary[name] = {
            'status': 'supported-by-contracts' if any(not p['conditions'] for p in found) else
                      'conditional' if found else 'no-path-within-model-and-bounds',
            'path_count': len(found),
            'gaps': [gap(p, v, root['facts'][p]) for p, v in sorted(requirements.items())
                     if root['facts'][p] is not v],
        }
    result = {'schema_version': 1, 'kind': 'generated-capability-potential', 'inputs': inputs,
              'nodes': nodes, 'blocked': blocked, 'paths': paths, 'potential': potential,
              'goals': goal_summary, 'limits': limits, 'new_compiler_calls': 0,
              'training_eligible': False, 'global_impossibility_established': False,
              'scope': 'bounded reachability under supplied contracts; inferred states are not observations'}
    result['sha256'] = digest(result)
    return result


def validate(report):
    try:
        rebuilt = generate(**report['inputs'])
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('invalid generated capability structure') from exc
    if rebuilt != report:
        raise ValueError('generated capability report differs from retained inputs')
    return report
