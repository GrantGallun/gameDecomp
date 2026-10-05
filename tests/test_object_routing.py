"""Object-layer routing (solver.repair_queue.object_route): a named lever opens operand repair even when the diff
shows no faults, and a `certify` route spends no C-edit budget (eval/results/hidden-object-20260930)."""
from pathlib import Path  # noqa: F401  (campaign.accept takes a Path in sibling tests)

from eval import completion_campaign as campaign
from solver import repair_queue as queue


def node(semantic=None, **kw):
    return {'status': 'pending', 'jobs': [], 'source_sha256': 'source',
            'instruction_count': 20, 'dag_level': 0,
            'residual': {'compiled': True, 'frontend': {'passed': True}},
            'semantic_validation': semantic, **kw}


def revalidated(n):
    n['jobs'].append({'profile': 'revalidate@' + queue.semantic_environment_digest(),
                      'source_sha256': n['source_sha256']})
    return n


def state(nodes, **config):
    return {'config': {'scheduler': 'evidence-v1', 'model_calls': 2, **config}, 'nodes': nodes}


def test_object_lever_opens_operand_repair_despite_an_empty_fault_vector():
    # Break caught: guMtxIdent (2026-09-30) -- empty diff, all-zero faults, so operand_profile declined the one node
    # whose only fault (an unused file-scope object) its stream can fix.
    n = revalidated(node({'status': 'observed_pass_with_execution_debt'}, source='f.c'))
    n['residual'].update(faults={k: 0 for k in ('structural', 'offset', 'relocation')})
    assert queue.operand_profile(n) is None
    n['residual']['object'] = {'route': 'generator', 'levers': ['unused_file_scope_object']}
    assert queue.operand_profile(n)['operand_repair'] is True
    assert campaign.choose(state({'f': n}))[1]['name'].startswith('operand_repair@')


def test_certify_route_spends_no_c_edit_budget():
    # .text identical and only certificate artifacts left: no C edit can move it (func_8005905C/func_8005C14C
    # absorbed ~1,060 attempts this way).
    n = revalidated(node({'status': 'observed_pass_with_execution_debt'}, source='f.c'))
    assert campaign.choose(state({'f': n})) is not None
    n['residual']['object'] = {'route': 'certify', 'levers': []}
    assert campaign.choose(state({'f': n})) is None
    # a c_edit route (text differs) keeps the ordinary lanes
    n['residual']['object'] = {'route': 'c_edit', 'levers': []}
    assert campaign.choose(state({'f': n})) is not None
