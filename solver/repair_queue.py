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


_CERTIFICATE_DIGEST = None
_ADDRESS_LITERALS = {}


def certificate_digest():
    """Identity of the certificate code: a change re-certifies score-100 nodes once."""
    global _CERTIFICATE_DIGEST
    if _CERTIFICATE_DIGEST is None:
        import hashlib
        from pathlib import Path
        here = Path(__file__).resolve().parent
        digest = hashlib.sha256()
        # object_discrepancy routes the verdict (2026-09-30): a change re-certifies once, which also gives
        # retained packets their `object` route -- without it a zero-fault node is never re-scored.
        for name in ('byte_certificate.py', 'function_boundary.py', 'object_discrepancy.py'):
            digest.update((here / name).read_bytes())
        _CERTIFICATE_DIGEST = digest.hexdigest()[:16]
    return _CERTIFICATE_DIGEST


_SEMANTIC_DIGEST = None


def semantic_environment_digest():
    """Identity of the differential-execution environment code: a change re-validates failing nodes once."""
    global _SEMANTIC_DIGEST
    if _SEMANTIC_DIGEST is None:
        import hashlib
        from pathlib import Path
        here = Path(__file__).resolve().parent
        digest = hashlib.sha256()
        for path in (here / 'pointer_contracts.py', here / 'callee_execution.py', here / 'mips_differential.py',
                     here.parent / 'eval' / 'semantic_lane.py'):
            digest.update(path.read_bytes() if path.exists() else b'')
        _SEMANTIC_DIGEST = digest.hexdigest()[:16]
    return _SEMANTIC_DIGEST


def has_address_literals(node):
    """Pointer-cast address literals in the node's current source (memoized by source hash)."""
    key = node.get('source_sha256')
    if key not in _ADDRESS_LITERALS:
        from pathlib import Path
        from solver import address_symbols
        try:
            source = Path(node['source']).read_text()
        except (OSError, KeyError, TypeError):
            source = ''
        # `(void *)0` and small offsets are never linker-symbol addresses.
        _ADDRESS_LITERALS[key] = any(int(m.group('lit'), 0) >= 0x10000
                                     for m in address_symbols.POINTER_CAST.finditer(source))
    return _ADDRESS_LITERALS[key]


_STRUCTURAL_SHAPES = {}


def has_structural_shapes(node):
    """Whether a structural_mutations family fires on the node's source (memoized by source hash)."""
    key = node.get('source_sha256')
    if key not in _STRUCTURAL_SHAPES:
        from pathlib import Path
        from solver import structural_mutations
        try:
            _STRUCTURAL_SHAPES[key] = structural_mutations.signals(Path(node['source']).read_text())
        except (OSError, KeyError, TypeError):
            _STRUCTURAL_SHAPES[key] = False
    return _STRUCTURAL_SHAPES[key]


_STRING_LITERALS = {}
_PLACEHOLDERS = {}
_LOWERING = {}


def _source_flag(cache, node, test):
    key = node.get('source_sha256')
    if key not in cache:
        from pathlib import Path
        try:
            cache[key] = test(Path(node['source']).read_text())
        except (OSError, KeyError, TypeError):
            cache[key] = False
    return cache[key]


def census_profile(node):
    """Zero-model repairs measured on the 2026-09-14 failure census, before any lane work."""
    if node.get('status') != 'pending' or not node.get('source_sha256'):
        return None
    residual = node.get('residual') or {}
    if residual.get('compiled') is not True or (residual.get('frontend') or {}).get('passed') is False:
        return None
    done = {(j.get('profile'), j.get('source_sha256')) for j in node.get('jobs', [])}
    faults = {k for k, v in (residual.get('faults') or {}).items() if v}
    # Relocation-only residuals can be spelling differences the ROM-backed certificate accepts.
    if (node.get('score') or 0) >= 100.0 or (faults and faults <= {'relocation'}):
        name = 'recertify@' + certificate_digest()
        if (name, node['source_sha256']) not in done:
            return {'name': name, 'model': False, 'deterministic_budget': 0}
    semantic = (node.get('semantic_validation') or {}).get('status')
    if semantic in ('observed_failure', 'inconclusive'):
        name = 'revalidate@' + semantic_environment_digest()
        if (name, node['source_sha256']) not in done:
            return {'name': name, 'model': False, 'deterministic_budget': 0}
    if (residual.get('faults') or {}).get('relocation') and has_address_literals(node):
        if ('address_symbols', node['source_sha256']) not in done:
            return {'name': 'address_symbols', 'model': False, 'deterministic_budget': 0, 'address_rounds': 3}
    # Measured population: 90+ functions (13 hits); broader string-literal nodes mostly decline.
    if ((residual.get('faults') or {}).get('relocation') and (node.get('score') or 0) >= 90
            and ('named_rodata', node['source_sha256']) not in done):
        from solver import address_symbols
        if _source_flag(_STRING_LITERALS, node, lambda s: address_symbols.STRING.search(s) is not None):
            return {'name': 'named_rodata', 'model': False, 'deterministic_budget': 0, 'address_rounds': 3}
    from solver import stack_layout
    if stack_layout.signals(residual) and ('stack_layout', node['source_sha256']) not in done:
        return {'name': 'stack_layout', 'model': False, 'deterministic_budget': 0, 'stack_rounds': 4}
    if has_structural_shapes(node) and ('structural_rewrites', node['source_sha256']) not in done:
        return {'name': 'structural_rewrites', 'model': False, 'deterministic_budget': 0, 'structural_rounds': 3}
    return None


def binary_input_revision(state):
    """One revision for this campaign's pinned binary, SDK and generator."""
    if state.get('pins') and state.get('config', {}).get('repo'):
        from pathlib import Path
        from solver import binary_type_draft
        return binary_type_draft.input_revision(Path(state['config']['repo']), state['pins'])
    return None


_OPERAND_REPAIR_DIGEST = None


def operand_repair_digest():
    """Version the delivered operand route; indirect owner changes require a bump."""
    global _OPERAND_REPAIR_DIGEST
    if _OPERAND_REPAIR_DIGEST is None:
        import hashlib
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        digest = hashlib.sha256(b'operand-repair-v1')
        for name in ('eval/operand_repair.py', 'solver/regalloc_mutations.py',
                     'solver/branch_defaults.py',
                     'solver/local_web_merge.py', 'solver/c89.py',
                     'solver/repair_context.py', 'solver/diffrepair.py',
                     'solver/rodata_symbol.py', 'solver/byte_certificate.py',
                     'solver/source_attribution.py', 'solver/frontend_check.py',
                     'solver/workspace.py', 'solver/family_gates.py', 'solver/edit_locality.py',
                     'solver/file_scope_objects.py', 'solver/object_discrepancy.py'):
            digest.update(name.encode())
            digest.update((root / name).read_bytes())
        _OPERAND_REPAIR_DIGEST = digest.hexdigest()[:16]
    return _OPERAND_REPAIR_DIGEST


def object_route(node):
    """The residual's object route (solver.object_discrepancy.classify), or None for packets that predate it."""
    return ((node.get('residual') or {}).get('object') or {}).get('route')


def operand_profile(node):
    """One full-evidence repair visit on a measured small structural residual, or on an object lever.

    A lever the object rows name (object_route) is admitted even with an all-zero fault vector: the diff cannot show
    those residuals, so gating on diff faults alone declined exactly the nodes it owns (guMtxIdent, 2026-09-30)."""
    residual = node.get('residual') or {}
    faults = residual.get('faults')
    lever = object_route(node) == 'generator'
    if (node.get('status') != 'pending' or not node.get('source')
            or not node.get('source_sha256') or residual.get('compiled') is not True
            or (residual.get('frontend') or {}).get('passed') is not True
            or (not lever and (not isinstance(faults, dict) or not any(faults.values())
                               or faults.get('structural', 0) > 2))):
        return None
    revision = operand_repair_digest()
    name = 'operand_repair@' + revision
    if any(job.get('profile') == name for job in node.get('jobs', [])):
        return None
    return {'name': name, 'model': False, 'deterministic_budget': 0,
            'operand_repair': True, 'operand_budget': 72, 'operand_revision': revision}


_SITE_EDIT_DIGEST = None
SITE_EDIT_MAX_FAULTS = 12


def site_edit_digest():
    """Version the site-edit route by its generator and search sources; a changed operator is a new visit."""
    global _SITE_EDIT_DIGEST
    if _SITE_EDIT_DIGEST is None:
        import hashlib
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        digest = hashlib.sha256(b'site-edits-v1')
        for name in ('eval/site_edit_repair.py', 'solver/site_edits.py', 'solver/rewrites.py',
                     'solver/residual_sites.py', 'solver/source_attribution.py', 'solver/signals.py',
                     'solver/diffrepair.py', 'solver/c89.py', 'solver/code_shapes.py', 'solver/workspace.py',
                     # the shape and mined lanes (2026-09-29): a changed generator or rule table is a new visit
                     'solver/branch_shape.py', 'solver/unaligned_copy.py', 'solver/temp_copyback.py',
                     'solver/counted_loop.py', 'solver/residual_classes.py', 'solver/rewrite_library.py',
                     'solver/rule_miner.py', 'patterns/equivalences.py', 'patterns/mined_rules.json',
                     'solver/term_rewrite.py'):
            digest.update(name.encode())
            digest.update((root / name).read_bytes())
        _SITE_EDIT_DIGEST = digest.hexdigest()[:16]
    return _SITE_EDIT_DIGEST


def site_edit_profile(node):
    """One localized-typed-edit visit on a compiled, frontend-valid, small residual.

    Measured (eval/results/site-edits-20260929): 13 exact of 77 functions whose residual had at most six
    diff lines; 0 of 13 larger argument-register functions. So only small residuals are scheduled.
    """
    residual = node.get('residual') or {}
    faults = residual.get('faults')
    if (node.get('status') != 'pending' or not node.get('source') or not node.get('source_sha256')
            or residual.get('compiled') is not True
            or (residual.get('frontend') or {}).get('passed') is not True
            or not isinstance(faults, dict) or not any(faults.values())):
        return None
    total = sum(v for k, v in faults.items() if k != 'layout' and isinstance(v, int))
    if total > SITE_EDIT_MAX_FAULTS:
        return None
    revision = site_edit_digest()
    name = 'site_edits@' + revision
    if any(job.get('profile') == name for job in node.get('jobs', [])):
        return None
    return {'name': name, 'model': False, 'deterministic_budget': 0,
            'site_edits': True, 'site_edit_budget': 72, 'site_edit_revision': revision}


_PLATEAU_DIGEST = None


def plateau_digest():
    """Version of the plateau route: its search, its runner and every generator family it draws on."""
    global _PLATEAU_DIGEST
    if _PLATEAU_DIGEST is None:
        import hashlib
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        digest = hashlib.sha256(b'plateau-v1')
        for name in ('eval/plateau_repair.py', 'solver/plateau_search.py', 'solver/regalloc_mutations.py'):
            digest.update(name.encode())
            digest.update((root / name).read_bytes())
        digest.update(site_edit_digest().encode())
        _PLATEAU_DIGEST = digest.hexdigest()[:16]
    return _PLATEAU_DIGEST


def plateau_profile(node):
    """One plateau-search visit per source, after the site-edit visit of the current revision.

    Measured (eval/results/loop-shape-20260930): 5 exact of 219 near misses the site-edit search had left,
    in 47,345 compiles. Gated like the site-edit route (small, compiled, frontend-valid residual) and only
    once that route has visited, so the cheaper search always runs first.
    """
    residual = node.get('residual') or {}
    faults = residual.get('faults')
    if (node.get('status') != 'pending' or not node.get('source') or not node.get('source_sha256')
            or residual.get('compiled') is not True
            or (residual.get('frontend') or {}).get('passed') is not True
            or not isinstance(faults, dict) or not any(faults.values())):
        return None
    if sum(v for k, v in faults.items() if k != 'layout' and isinstance(v, int)) > SITE_EDIT_MAX_FAULTS:
        return None
    jobs = node.get('jobs', [])
    if not any(job.get('profile') == 'site_edits@' + site_edit_digest() for job in jobs):
        return None
    name = 'plateau@' + plateau_digest()
    if any(job.get('profile') == name and job.get('source_sha256') == node['source_sha256'] for job in jobs):
        return None
    return {'name': name, 'model': False, 'deterministic_budget': 0, 'plateau': True,
            'plateau_budget': 240, 'plateau_revision': plateau_digest()}


def next_profile(node, model_calls, legacy_profiles, binary_revision=None):
    phase = lane(node)
    if phase in {Lane.DONE, Lane.BLOCKED}:
        return None
    if phase in {Lane.BYTE, Lane.ENVIRONMENT} and object_route(node) == 'certify':
        # .text is byte-identical and every remaining row is a certificate/integration artifact
        # (object_discrepancy.EXPLAINED): no C edit can move it. func_8005905C and func_8005C14C absorbed
        # ~1,060 campaign attempts this way while their .text already matched (2026-09-30).
        return None
    key = evidence_key(node)
    census = census_profile(node) if phase not in {Lane.INTAKE, Lane.FRONTEND} else None
    if census is not None:
        return {**census, 'lane': phase.value, 'evidence_key': key}
    operand = operand_profile(node)
    if operand is not None and object_route(node) == 'generator' and phase in {Lane.BYTE, Lane.ENVIRONMENT}:
        # A confirmed object lever names its own fix; take that one visit before any search.
        return {**operand, 'lane': phase.value, 'evidence_key': key}
    site = site_edit_profile(node)
    plateau = plateau_profile(node) if site is None else None
    binary_profile = None
    if node.get('source_sha256') and node.get('source'):
        from solver import binary_type_draft
        revision = binary_revision or binary_type_draft.code_digest()
        name = 'binary_types@' + revision
        # The pinned binary/public inputs and generator own this visit;
        # another incumbent spelling does not reset its budget.
        if node.get('binary_type_revision') != revision and not any(
                j.get('profile') == name for j in node.get('jobs', [])):
            binary_profile = {'name': name, 'model': False, 'binary_types': True, 'binary_type_revision': revision, 'deterministic_budget': 0,
                    'lane': phase.value, 'evidence_key': key}
    used = {j['profile'] for j in node.get('jobs', [])
            if j.get('evidence_key') == key}
    # Deterministic zero-model search repeats itself on an unchanged source: 2026-09-14, 1,537 of
    # 3,905 local_rewrites jobs re-ran a source while the node cycled between equal-score alternates.
    zero_model = {p['name'] for p in legacy_profiles if not p['model']}
    used |= {j['profile'] for j in node.get('jobs', [])
             if j['profile'] in zero_model and j.get('source_sha256') == node.get('source_sha256')}
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
        done = {(j.get('profile'), j.get('source_sha256')) for j in node.get('jobs', [])}
        if ((node.get('residual') or {}).get('compiled') is False
                and ('placeholder_recovery', node.get('source_sha256')) not in done):
            from solver import placeholder_declarations
            if _source_flag(_PLACEHOLDERS, node, placeholder_declarations.signals):
                return {'name': 'placeholder_recovery', 'model': False, 'deterministic_budget': 0,
                        'lane': phase.value, 'evidence_key': key}
        if ((node.get('residual') or {}).get('compiled') is False
                and ('draft_lowering', node.get('source_sha256')) not in done):
            from solver import void_pointer_units
            if _source_flag(_LOWERING, node, void_pointer_units.source_signals):
                return {'name': 'draft_lowering', 'model': False, 'deterministic_budget': 0,
                        'lane': phase.value, 'evidence_key': key}
        if (node.get('residual') or {}).get('compiled') is True:
            # Casts at clang's diagnosed ranges; zero-model, once per evidence key.
            profiles.insert(0, {'name': 'frontend_fixits', 'model': False, 'deterministic_budget': 0,
                                'frontend_fixits': True})
    else:
        profiles = [p for p in legacy_profiles if not p.get('type_transaction')]
        from solver import regalloc_search
        dominant = regalloc_search.register_dominant((node.get('residual') or {}).get('faults') or {})
        profiles = [p for p in profiles if p['name'] != 'regalloc_search' or dominant]
        if phase == Lane.ENVIRONMENT:
            # Exact-object search can still succeed without runtime execution.
            # Do not spend model calls pretending there is a counterexample.
            profiles = [p for p in profiles if not p['model']]
    if operand is not None or site is not None or plateau is not None or binary_profile is not None:
        # Preserve measured cheap repairs, but try a fresh binary draft before
        # spending model calls. Exhausted deterministic nodes regain one visit.
        first_model = next((i for i, p in enumerate(profiles) if p['model']), len(profiles))
        profiles[first_model:first_model] = [p for p in (site, plateau, operand, binary_profile) if p is not None]
    profiles = [p for p in profiles if not p.get('compiler_localization') or (
        phase == Lane.BYTE and (node.get('residual') or {}).get('compiled') is True)]
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
    binary_revision = binary_input_revision(state)
    for name, node in nodes.items():
        if state['config'].get('compile_sweep') and phases[name] not in {Lane.INTAKE, Lane.FRONTEND}:
            continue
        if state['config'].get('scheduler') == 'investigation-v1':
            from solver.investigation import profile as investigation_profile
            profile = investigation_profile(node, state['config']['model_calls'], legacy_profiles,
                                            state['config'].get('investigation_policy'), binary_revision)
        else:
            profile = next_profile(node, state['config']['model_calls'], legacy_profiles, binary_revision)
        if profile is not None:
            if profile.get('investigation_policy'):
                profile['capability_issues'] = {key: issue for key, issue in issues.items()
                    if name in issue['affected_functions'] and len(issue['affected_functions']) >= 2}
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
        band = len(node.get('jobs', [])) // 2
        if profile.get('site_edits'):
            # Measured 13/77 exact on small residuals (site-edits-20260929); one visit per revision.
            band = -1
        if profile.get('plateau'):
            # Measured 5/219 exact on near misses the site-edit search left (loop-shape-20260930).
            band = -1
        if profile.get('operand_repair'):
            # The measured <=2-structural-fault operand panel has a new route;
            # prior visits to other generators must not bury this one visit.
            band = -1
        if profile['name'] == 'regalloc_search':
            from solver import regalloc_search
            # Measured 2026-09-13: 73/78 register-only and 63/145 with <=2 other faults went
            # object-exact offline. Run that set ahead of its visit band; it is CPU-only.
            if regalloc_search.register_dominant((node.get('residual') or {}).get('faults') or {}, max_other=2):
                band = -1
        if profile['name'] == 'structural_rewrites' and sum(((node.get('residual') or {}).get('faults') or {}).values()) <= 40:
            # Measured 2026-09-14: 89 of 356 improved; the near/close tiers gain most.
            band = -1
        if profile['name'].startswith('revalidate@') and \
                (node.get('semantic_validation') or {}).get('status') == 'observed_failure':
            # 2026-09-14: stale stack-offset failures kept 66 nodes in model-only semantic jobs.
            band = -1
        if profile['name'] == 'address_symbols' or profile['name'].startswith('recertify@'):
            # Measured 2026-09-14: 4 exact + 22 improved of 27 (address symbols); 5 of 5 recertified.
            band = -1
        if profile['name'] in ('stack_layout', 'placeholder_recovery', 'draft_lowering'):
            # Measured 2026-09-14 (ninety-census): stack layout, named rodata, placeholder declarations.
            band = -1
        if profile['name'] == 'frontend_fixits':
            # Measured 2026-09-14: 28/29 compiled frontend-rejected functions passed after fix-its.
            band = -1
        priority = (band, order[phase], depth, -leverage, locality,
                    unit_rank.get(tu_index.get(name), unknown_unit) if tu_index else 0,
                    node.get('instruction_count') or 0, name)
        item = WorkItem(name, phase.value, profile['name'], profile['evidence_key'], priority,
                        tu_index.get(name), depth)
        items.append(item)
        profiles[name] = profile
    heap = [(item.priority, item.function) for item in items]
    if state['config'].get('scheduler') == 'investigation-v1':
        for key, issue in issues.items():
            task = state.get('capability_tasks', {}).get(key) or state['config'].get('capability_tasks', {}).get(key)
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
