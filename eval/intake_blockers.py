"""Two inventories: compiler admission and compiled object mismatches.

Diagnostic classes and sites are observations, not inferred independent causes
or a count of edits to completion. Owner routes are conditional; trace contains
what actually happened on each source hash.
"""
from collections import Counter, defaultdict
from dataclasses import asdict

from eval.intake_probe import classify_residual
from solver import signals
from solver.frontend_diagnostics import errors_are_complete


# Existing owners worth inspecting and the evidence they still require. Empty
# lists explicitly mean there is no direct automatic owner in this intake route.
ROUTES = {
    'member-on-void': (['void_members'], 'diagnostic-bound binary base identity and unanimous access width'),
    'member-on-scalar-or-array': (['global_fields', 'scalar_member_index', 'negative_offset'], 'diagnosed named global with witnessed offset/width, or supported typed-pointer unk-offset expression'),
    'undeclared-member': (['global_fields'], 'diagnosed named-global unk offset and unanimous target access width; other member bases remain unresolved'),
    'non-pointer-subscript': (['stack_arrays'], 'contiguous constant-index word stack storage with target slot witnesses and supported aliases; other bases remain unresolved'),
    'non-pointer-arrow': ([], 'recover value versus pointer representation before choosing an access'),
    'array-assignment': (['frontend_abi'], 'witnessed zero element store; other copies or assignments need binary recovery'),
    'call-arity': (['frontend_abi', 'call_arity'], 'supported header/ABI packing or excess-word projection; missing arguments and unsupported contracts remain unresolved'),
    'invalid-binary-operands': (['global_scalars', 'or_address', 'ido_byte_cursors'], 'unanimous named-global scalar read, supported address-OR shape or frontend-passing byte cursor; other shapes unowned'),
    'incompatible-int-pointer': (['widen_pointer_declarations', 'frontend_casts'], 'diagnosed target type and valid expression ranges'),
    'incompatible-pointer': (['widen_pointer_declarations', 'frontend_casts'], 'diagnosed target type and valid expression ranges'),
    'incomplete-definition': (['source_type_declarations', 'frontend_casts'], 'available declaration or diagnosed incomplete-pointer arithmetic'),
    'unknown-type-name': (['source_type_declarations', 'header_variant'], 'available type declaration'),
    'undeclared-function': (['header_prototypes'], 'evidenced compatible prototype'),
    'undeclared-identifier': (['undeclared_identifiers', 'globals_variant'], 'symbol identity and compatible declaration'),
    'redeclaration/conflict': (['header_variant', 'header_signature'], 'compatible headers or own definition with matching supported ABI widths and typed body aliases'),
}
OBJECT_AXES = ('offset', 'width', 'structural', 'reloc', 'regalloc', 'ordering', 'immediate')


def inventory(rows):
    stages, front_states, front_counts = Counter(), Counter(), Counter()
    object_states, object_counts, declines = Counter(), Counter(), Counter()
    output = []
    for row in rows:
        verdict, front = row['verdict'], row['frontend']
        if front.get('status') == 'unavailable':
            stage = 'frontend-unavailable'
        elif not errors_are_complete(front):
            stage = 'frontend-incomplete'
        elif verdict.get('exact'):
            stage = 'object-exact' if front.get('status') == 'passed' else 'object-exact-frontend-blocked'
        elif verdict.get('compiled') and front.get('status') == 'passed':
            stage = 'object-mismatch'
        else:
            stage = 'compilation-blocked'
        stages[stage] += 1
        counts = Counter(classify_residual(e['what']) for e in front.get('errors', []))
        front_states.update(counts.keys())
        front_counts.update(counts)
        sites = defaultdict(list)
        lines = row.get('source', '').splitlines()
        for e in front.get('errors', []):
            sites[(e.get('file', 'candidate.c'), e.get('line'), e.get('column'))].append(e['what'])
        profile = None
        if verdict.get('compiled') and not verdict.get('exact'):
            profile = asdict(signals.analyse(verdict.get('diff') or '', score=verdict.get('score') or 0))
            if stage == 'object-mismatch':
                for axis in OBJECT_AXES:
                    if profile[axis]:
                        object_states[axis] += 1
                        object_counts[axis] += profile[axis]
                if not any(profile[axis] for axis in OBJECT_AXES):
                    object_states['unresolved-certificate-or-diff'] += 1
                    object_counts['unresolved-certificate-or-diff'] += 1
        for t in row.get('trace', []):
            if not t.get('changed'):
                declines[(t['action'], t.get('reason') or 'unspecified')] += 1
        output.append(dict(function=row['function'], stage=stage, compiled=bool(verdict.get('compiled')),
            exact=bool(verdict.get('exact')), score=verdict.get('score'), frontend=front.get('status'),
            diagnostics_complete=errors_are_complete(front), unparsed_errors=front.get('unparsed_errors', []),
            classes=dict(counts), routes={c: dict(owners=ROUTES.get(c, ([], 'no direct intake owner'))[0],
                prerequisite=ROUTES.get(c, ([], 'no direct intake owner'))[1]) for c in counts},
            sites=[dict(file=file, line=line, column=col,
                        source_line=lines[line-1] if file == 'candidate.c' and line and 0 < line <= len(lines) else None,
                        diagnostics=messages) for (file, line, col), messages in sites.items()],
            object_profile=profile, ido_diagnostics=verdict.get('stderr') or '', trace=row.get('trace', [])))
    return dict(denominator=len(output), stages=dict(stages),
        frontend_classes={c: dict(functions=front_states[c], diagnostics=front_counts[c]) for c in front_states},
        object_classes={c: dict(functions=object_states[c], observations=object_counts[c]) for c in object_states},
        declines=[dict(action=a, reason=r, invocations=n) for (a, r), n in declines.most_common()], rows=output)
