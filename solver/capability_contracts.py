"""Declared intended capabilities, distinct from measured successes.

These are reviewable specifications, not formal correctness proofs. A candidate
contract promises a search representation, never that a compiler will match it.
"""
from copy import deepcopy
import hashlib
from pathlib import Path

from eval.search_replay import digest


def _row(id, owner, output, covers, domain, requires, callers, test, wiring, limit):
    return dict(id=id, owner=owner, output=output, covers=covers, domain=domain,
                requires=requires, callers=callers, test=test, wiring=wiring, limitation=limit)


CONTRACTS = [
    _row('cfg', 'solver.cfg', 'control-flow-facts', ['control'], [], ['assembly_available'],
         ['theory','compile-recovery','model-repair'], 'tests/test_cfg.py', ['solver/dataflow.py'],
         'Unresolved indirect successors remain unknown; a CFG does not construct C.'),
    _row('dataflow', 'solver.dataflow', 'access-and-call-facts', ['integer','memory','calls'], [], ['assembly_available'],
         ['theory','compile-recovery','model-repair'], 'tests/test_dataflow.py', ['solver/compile_obligations.py'],
         'Unresolved addresses and joins are not recovered types or layouts.'),
    _row('m2c_draft', 'solver.m2c_input', 'c-candidate', ['integer','memory','control','calls','float'],
         ['draftable_subset'], ['assembly_available','big_endian_o32','m2c_available'],
         ['theory','compile-recovery'], 'tests/test_m2c_input.py', ['solver/compile_recovery.py','solver/theory_repairs.py'],
         'Expected draft construction only; source validity, ABI composition and exactness need checking.'),
    _row('header_signature', 'solver.header_signature_view', 'c-candidate', ['signature'],
         ['signature_shape_supported','signature_change_needed','signature_conflict_site','signature_edit_sites_supported'], ['header_abi_locked','big_endian_o32','source_bound_feedback'],
         ['theory','model-repair'], 'tests/test_header_signature_view.py', ['solver/theory_repairs.py','solver/modelrepair.py'],
         'Corresponding supported o32 widths, at most eight parameters, ordinary declarations/returns; no general wide ABI adaptation.'),
    _row('void_members', 'solver.void_field_repair', 'c-candidate', ['members'],
         ['void_member_diagnostic'], ['unambiguous_member_access','source_bound_feedback'],
         ['theory','model-repair'], 'tests/test_void_field_repair.py', ['solver/theory_repairs.py','solver/modelrepair.py'],
         'Requires matching target offset/width and a supported use; no invented record layout.'),
    _row('scalar_members', 'solver.scalar_member_index', 'c-candidate', ['members'],
         ['indexable_member_base'], ['scalar_member_site_supported','source_bound_feedback'],
         ['theory'], 'tests/test_scalar_member_index.py', ['solver/theory_repairs.py'],
         'Known divisible element width and an indexable base; a scalar local does not become an array by respelling an access.'),
    _row('global_fields', 'solver.global_field_view', 'c-candidate', ['members'],
         ['global_member_diagnostic'], ['header_declarations','unambiguous_member_access','source_bound_feedback'],
         ['theory','model-repair'], 'tests/test_global_field_view.py', ['solver/theory_repairs.py','solver/modelrepair.py'],
         'Included global declaration and matching symbol/offset/width; local pointer members are a different domain.'),
    _row('frontend_abi', 'solver.frontend_repair', 'c-candidate', ['calls','other'],
         ['frontend_idiom_supported'], ['source_bound_feedback','big_endian_o32','header_declarations'],
         ['theory','model-repair'], 'tests/test_intake_frontend_abi.py', ['solver/theory_repairs.py','solver/modelrepair.py'],
         'Closed diagnostic/target idioms only; not a general ABI synthesizer.'),
    _row('byteview_redraft', 'solver.compile_recovery', 'c-candidate', ['members','signature','calls'],
         ['byteview_grammar_supported'], ['assembly_available','m2c_available','header_abi_locked'],
         ['theory','compile-recovery'], 'tests/test_compile_recovery.py', ['solver/theory_repairs.py','solver/compile_recovery.py'],
         'Public ABI is locked; fresh byte views may leave calls unresolved or leave another repair grammar.'),
    _row('type_layouts', 'solver.type_constraints', 'measured-layout-candidates', ['members'],
         ['type_plan_domain_supported'], ['header_declarations','target_available','layout_probe_available'],
         ['compile-recovery','model-repair'], 'tests/test_type_constraints.py', ['solver/compile_recovery.py','solver/modelrepair.py'],
         'Target compiler measures included-header layouts; type assignment and union choices remain hypotheses.'),
    _row('wide_parameters', 'solver.wide_parameter_repair', 'c-candidate', ['signature'],
         ['single_named_wide_parameter'], ['header_abi_locked','big_endian_o32','wide_parameter_typedef'],
         ['compile-recovery'], 'tests/test_wide_parameter_repair.py', ['solver/compile_recovery.py'],
         'One header-wide parameter split into named _unk0/_unk4 locals; not mixed multi-parameter signatures.'),
    _row('wide_operations', 'solver.wide_reconstruction', 'c-candidate', ['wide_calls'],
         ['named_wide_fields','closed_wide_idiom'], ['measured_layouts'],
         ['compile-recovery'], 'tests/test_wide_reconstruction.py', ['solver/compile_recovery.py'],
         'Unsigned eight-byte measured fields and closed named high/low idioms; arbitrary byte-view expressions are outside this direct constructor.'),
    _row('wide_returns', 'solver.wide_return_repair', 'c-candidate', ['wide_returns'],
         ['closed_wide_return'], ['admitted_callee_evidence','big_endian_o32'],
         ['model-repair'], 'tests/test_wide_return_repair.py', ['solver/modelrepair.py'],
         'Admitted binary callees and fully accounted temporary uses; no unrestricted prototype change.'),
    _row('call_arity', 'solver.call_arity_repair', 'c-candidate', ['calls'],
         ['word_call_projection'], ['header_declarations','assembly_available','source_bound_feedback','big_endian_o32'],
         ['model-repair'], 'tests/test_call_arity_repair.py', ['solver/modelrepair.py'],
         'Word-sized projection hypotheses only; 64-bit arguments require another construction.'),
    _row('storage_search', 'solver.storage_repairs', 'c-candidate', ['object_residual'],
         ['storage_idiom_supported'], ['compiled_candidate'],
         ['theory'], 'tests/test_storage_repairs.py', ['solver/regalloc_mutations.py','eval/repair_planner.py'],
         'Bounded storage/address/parameter variants; complete inverse compilation is not implemented.'),
    _row('model_edits', 'solver.modelrepair', 'c-candidate', ['integer','memory','control','calls','members','signature','other','object_residual'],
         ['editable_source_slots'], ['model_available','source_bound_feedback'],
         ['model-repair'], 'tests/test_modelrepair.py', ['eval/agentrepair.py'],
         'Bounded model-proposed edits; correct inference implementation does not imply omniscient weights, complete candidate search or correct semantics.'),
    _row('semantic_check', 'solver.mips_differential', 'finite-behavioral-evidence', ['semantic_equivalence'],
         ['execution_domain_supported'], ['compiled_candidate','execution_environment','semantic_evaluator_configured'],
         ['model-repair'], 'tests/test_mips_differential.py', ['solver/modelrepair.py','eval/semantic_lane.py','eval/agentrepair.py'],
         'Function runner with bounded cases and admitted calls; not full-console emulation or all-input proof.'),
    _row('object_check', 'solver.byte_certificate', 'exact-object-decision', ['exact_object'], [],
         ['compiled_candidate','target_available','compiler_recipe'],
         ['theory','compile-recovery','model-repair'], 'tests/test_byte_certificate.py', ['solver/workspace.py'],
         'Checks a concrete object; it cannot construct a matching candidate or prove search completeness.'),
]


def catalog(root=None):
    """Pin the implementation/test/wiring boundary behind each intended contract."""
    root = Path(root or Path(__file__).resolve().parents[1])
    rows = deepcopy(CONTRACTS)
    for row in rows:
        paths = [row['owner'].replace('.', '/')+'.py', row.pop('test'), *row.pop('wiring')]
        row['references'] = [{'path': p, 'sha256': hashlib.sha256((root/p).read_bytes()).hexdigest()} for p in paths]
    result = {'schema_version':1, 'authority':'developer-authored-intended-contracts',
              'scope':'declared component subset of the solver; unlisted machinery is unassessed',
              'contracts':rows, 'formal_proof':False}
    result['sha256'] = digest(result)
    return result


def validate_catalog(value):
    if value.get('sha256') != digest({k:v for k,v in value.items() if k!='sha256'}):
        raise ValueError('capability catalogue checksum mismatch')
    declared = [{k:v for k,v in row.items() if k not in {'test','wiring'}} for row in CONTRACTS]
    actual = [{k:v for k,v in row.items() if k!='references'} for row in value['contracts']]
    if declared != actual or value.get('formal_proof') is not False:
        raise ValueError('capability catalogue differs from declared contracts')
    for original,row in zip(CONTRACTS,value['contracts']):
        expected=[original['owner'].replace('.','/')+'.py',original['test'],*original['wiring']]
        if [r['path'] for r in row['references']] != expected:
            raise ValueError('capability references differ from declared owners')
    return value
