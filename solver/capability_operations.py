"""Instantiate transition contracts from a validated, source-bound assessment.

Primitive contracts remain authored specifications. Their compositions and
potential are generated; unmodeled transformations are not invented as edges.
"""
from copy import deepcopy

from solver.capability_map import validate_assessment
from solver.capability_potential import generate


# These facts concern the fixed target/tool environment, not a candidate's text.
# Everything else is conservatively forgotten after a C mutation unless produced.
ENVIRONMENT = frozenset({
    'assembly_available', 'draftable_subset', 'big_endian_o32', 'm2c_available',
    'target_available', 'compiler_recipe', 'layout_probe_available', 'model_available',
    'execution_environment', 'semantic_evaluator_configured',
})


def from_assessment(assessment, *, max_depth=3, max_nodes=256):
    """Generate conditional paths without reading files or invoking any owner.

    `output.<owner>` means that owner's declared artifact was produced in a
    modeled transition. It never means it passed compilation or matched bytes.
    Even paths without missing predicates remain contract inferences.
    """
    validate_assessment(assessment)
    rows = assessment['capabilities']
    facts = deepcopy(assessment['predicates'])
    for row in rows:
        for predicate in row['domain'] + row['requires']:
            facts.setdefault(predicate, None)
        facts['wired.' + row['id']] = row['connected']
        facts['output.' + row['id']] = False
    facts.setdefault('measured_layouts', None)
    stable = ENVIRONMENT | {p for p in facts if p.startswith('wired.')}
    operations = []
    for row in rows:
        requires = {p: True for p in row['domain'] + row['requires']}
        requires['wired.' + row['id']] = True
        produces = {'output.' + row['id']: True}
        mutation = row['output'] == 'c-candidate'
        if mutation:
            # A changed source has no bound compiler/diagnostic receipt yet.
            produces.update(compiled_candidate=False, source_bound_feedback=False)
        if row['id'] == 'type_layouts':
            # Conditional on the measured-layout constructor's declared domain.
            # Concrete layout coverage and closed wide idioms still have their
            # own prerequisites in downstream contracts.
            produces['measured_layouts'] = True
        preserves = sorted((set(facts) & stable if mutation else set(facts)) - set(produces))
        invalidates = sorted(set(facts) - set(preserves) - set(produces))
        operations.append({'id': row['id'], 'requires': requires, 'produces': produces,
                           'preserves': preserves, 'invalidates': invalidates,
                           'owner': row['owner'], 'output_kind': row['output'],
                           'references': deepcopy(row['references']),
                           'limitation': row['limitation']})
    goals = {row['id']: {'output.' + row['id']: True} for row in rows if row['needed']}
    context = {key: assessment[key] for key in
               ('source_sha256', 'target_sha256', 'compiler_sha256', 'receipt_id')}
    context.update(assessment_sha256=assessment['sha256'],
                   catalog_sha256=assessment['inputs']['contracts']['sha256'],
                   caller=assessment['inputs']['connected'],
                   assistance=deepcopy(assessment['inputs']['context'].get('assistance')),
                   authority='declared operation contracts; conditional composition, not compiler evidence')
    return generate(facts, operations, goals, context=context, max_depth=max_depth, max_nodes=max_nodes)
