"""Retractable, evidence-supported explanations with explicit consumers.

This store lives in campaign checkpoints, never in the immutable binary tier.
Conflicting explanations coexist. Consumers receive candidates, not new types
or runtime contracts; source and semantic checks still decide their outcomes.
"""
from copy import deepcopy
from solver.evidence_schedule import fingerprint


def add(store, *, subject, alternatives, support, consumers, origin):
    if not subject or len(set(alternatives)) < 2 or not support or not consumers:
        raise ValueError('hypothesis requires a subject, competing alternatives, support and consumers')
    if any(key not in store.get('observations', {}) for key in support):
        raise ValueError('hypothesis cites unknown observations')
    value = {'subject': subject, 'alternatives': list(alternatives), 'support': sorted(set(support)),
             'consumers': sorted(set(consumers)), 'origin': origin}
    key = fingerprint(value)
    store.setdefault('hypotheses', {}).setdefault(key, {**value, 'status': 'proposed'})
    return key


def retract(store, observation, reason):
    """Invalidate dependents without deleting historical explanations."""
    if observation not in store.get('observations', {}):
        raise ValueError('unknown observation')
    store.setdefault('retractions', {})[observation] = reason
    affected = set()
    for hypothesis in store.get('hypotheses', {}).values():
        if observation in hypothesis['support']:
            hypothesis.update(status='retracted', reason=reason)
            affected.update(hypothesis['consumers'])
    return sorted(affected)


def context(store, function):
    rows = []
    for key, h in sorted(store.get('hypotheses', {}).items()):
        if h['status'] != 'proposed' or function not in h['consumers']:
            continue
        if any(s in store.get('retractions', {}) for s in h['support']):
            continue
        rows.append({'id': key, **h, 'support': [store['observations'][s] for s in h['support']]})
    return {'hypotheses': rows[:8], 'omitted': max(0, len(rows) - 8),
            'authority': 'competing explanations, not established C types or execution contracts'}


def consumers(conn, subject, eligible):
    """Share only an identical global address identity, never a register name."""
    if not subject.startswith('global:'):
        return []
    try:
        address = int(subject.split(':', 1)[1], 16)
    except ValueError:
        return []
    columns = {row[1] for row in conn.execute('PRAGMA table_info(evidence)')}
    if not {'base', 'func_addr'} <= columns:
        return []
    result = set()
    for base, function in conn.execute('SELECT DISTINCT e.base,f.name FROM evidence e '
                                       'JOIN functions f ON f.addr=e.func_addr WHERE e.base LIKE ?', ('global:%',)):
        try:
            same = int(base.split(':', 1)[1], 16) == address
        except ValueError:
            continue
        if same and function in eligible:
            result.add(function)
    return sorted(result)


def ingest(state, function, payload, conn):
    """Receipted worker observations enter one shared checkpoint transaction."""
    store = state.setdefault('shared_hypotheses', {})
    for row in payload.get('observations', []):
        key = row['id']
        if fingerprint({k: v for k, v in row.items() if k != 'id'}) != key:
            raise ValueError('changed investigation observation')
        previous = store.setdefault('observations', {}).get(key)
        if previous is not None and previous != row:
            raise ValueError('observation identity collision')
        store['observations'][key] = deepcopy(row)
    for claim in payload.get('hypotheses', []):
        # A shared claim must cite a binary observation of that exact subject.
        # A model cannot assign every function as a consumer by naming a type.
        supporting = [store.get('observations', {}).get(k, {}) for k in claim['support']]
        grounded = any(r.get('kind') == 'binary-observations' and
            any(o.get('base', '').lower() == claim['subject'].lower() for o in r.get('observations', []))
            for r in supporting)
        users = consumers(conn, claim['subject'], state['nodes']) if grounded else []
        add(store, subject=claim['subject'], alternatives=claim['alternatives'],
            support=claim['support'], consumers=users or [function], origin=function)
    changed = []
    for name, node in state['nodes'].items():
        packet = context(store, name)
        key = fingerprint(packet)
        if node.get('shared_evidence_sha256') != key:
            node.update(shared_context=packet, shared_evidence_sha256=key)
            changed.append(name)
    return changed
