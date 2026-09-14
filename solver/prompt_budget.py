"""Auditable prompt compaction and conservative, explicitly estimated headroom.

Full semantic receipts remain authoritative. Only known verbose observation
enumerations are elided; unknown fields and all binding obligations survive.
"""
import copy
import hashlib
import json
import os


def semantic_packet(packet, *, omit_secondary=False):
    """Compact prompt-only scaffolding; retain causal/path evidence and inputs.

    Complete receipts are unchanged. Known verbose callee trace excerpts and
    duplicated compiler invocation text become exact content references. ABI
    dictionaries use lossless column tables when smaller; unknown fields survive.
    """
    result = copy.deepcopy(packet)
    references = {}
    encode = lambda value: json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)

    def reference(container, key, path):
        if key not in container:
            return
        value = container[key]
        # Null/empty placeholders are already compact and carry useful meaning.
        if not value:
            return
        serialized = encode(value)
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        references[path] = {'sha256': digest, 'json_bytes': len(serialized.encode()),
                            **({'items': len(value)} if isinstance(value, list) else {})}
        del container[key]

    for name, contract in (result.get('call_contracts') or {}).items():
        if not isinstance(contract, dict):
            continue
        selection = contract.get('active_selection')
        if not isinstance(selection, dict):
            continue
        path = 'call_contracts.'+name+'.active_selection.'
        reference(selection, 'includes', path+'includes')
        recipe = selection.get('frontend_recipe')
        if isinstance(recipe, dict):
            for field in ('command', 'settings'):
                reference(recipe, field, path+'frontend_recipe.'+field)

    primary = result.get('primary_counterexample')
    if isinstance(primary, dict):
        executions = primary.get('concrete_callee_executions') or {}
        for side in ('target', 'candidate'):
            for index, call in enumerate((executions.get(side) or {}).get('calls', [])):
                if not isinstance(call, dict):
                    continue
                for field in ('instruction_prefix', 'instruction_suffix'):
                    reference(call, field, 'primary_counterexample.concrete_callee_executions.'+
                              side+'.calls.'+str(index)+'.'+field)
    if omit_secondary:
        # Drop whole lower-priority observations, never part of an input or
        # primary branch history. The ordinary evaluator still uses every case.
        reference(result, 'other_failure_classes', 'other_failure_classes')

    def table(mapping):
        if not isinstance(mapping, dict) or not mapping or not all(isinstance(v, dict) for v in mapping.values()):
            return mapping
        groups = {}
        for name, value in mapping.items():
            columns = tuple(sorted(value))
            groups.setdefault(columns, {})[name] = [value[k] for k in columns]
        projected = {'format': 'named-column-tables-v1',
            'tables': [{'columns': list(columns), 'rows': rows} for columns, rows in groups.items()]}
        return projected if len(encode(projected)) < len(encode(mapping)) else mapping

    if 'call_contracts' in result:
        result['call_contracts'] = table(result['call_contracts'])
    environment = result.get('callee_environment')
    if isinstance(environment, dict) and 'leaves' in environment:
        environment['leaves'] = table(environment['leaves'])
    result['_prompt_projection'] = {
        'version': 1, 'full_packet_sha256': hashlib.sha256(encode(packet).encode()).hexdigest(),
        'referenced_observations': references,
        'scope': ('Prompt projection only. Complete nested callee trace excerpts and compiler invocation text '
                  'remain in the source/panel-bound full receipt at the paths and hashes above. '
                  'Primary inputs, causal feedback, executed guards/operands, contracts and obligations remain. '
                  'Any secondary counterexamples omitted as whole observations are listed explicitly above. '
                  'In named-column-tables-v1 each named row supplies values in column order; '
                  'groups preserve different field sets, including missing versus null values.'),
    }
    return result


def semantic_summary(report):
    """Project a non-failing pass report without mutating its source receipt."""
    result = copy.deepcopy(report)
    if report.get('status') not in {'observed_pass', 'observed_pass_with_execution_debt'}:
        return result
    # Unexpected disagreements must retain the full packet, even if its status
    # was incorrectly labelled as passing by a producer.
    if report.get('feedback') or (report.get('outcome_accounting') or {}).get('observed_disagreements'):
        return result
    omitted = {}

    def remove_list(container, name, path):
        value = container.get(name)
        if isinstance(value, list):
            omitted[path + name] = len(value)
            del container[name]

    def coverage(container, path):
        if not isinstance(container, dict):
            return
        for name in ('missing_instructions', 'unresolved_branch_edges', 'infeasible_branch_edges'):
            remove_list(container, name, path)
        # Unresolved indirect jumps can bind an execution limitation; retain
        # their actual entries, as well as all counts, scope and unknown keys.

    for name in ('target_coverage', 'candidate_coverage'):
        coverage(result.get(name), name + '.')
    stress = result.get('stress_work')
    if isinstance(stress, dict):
        for name in ('selected_cases', 'examined_mutations'):
            remove_list(stress, name, 'stress_work.')
        coverage(stress.get('target_coverage'), 'stress_work.target_coverage.')
    exploration = result.get('exploration_trials')
    if isinstance(exploration, dict):
        remove_list(exploration, 'noncompleted_examples', 'exploration_trials.')
    remove_list(result, 'passed_case_indices', '')
    encoded = json.dumps(report, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    result['_prompt_projection'] = {
        'version': 1, 'full_report_sha256': hashlib.sha256(encoded).hexdigest(),
        'omitted_observation_counts': omitted,
        'scope': ('Finite observed pass, not universal equivalence. All execution debt and '
                  'source/call obligations below remain operative. Enumerated observational '
                  'cases and coverage entries remain in the source/panel-bound full receipt.'),
    }
    return result


class ContextBudgetError(ValueError):
    """The request cannot preserve its answer allowance under the size estimate."""

    def __init__(self, budget):
        self.context_budget = budget
        super().__init__(f"num_ctx headroom exceeded: estimated request needs "
                         f"{budget['required_tokens']} tokens including "
                         f"{budget['output_tokens']} output tokens; capacity is "
                         f"{budget['capacity']}. Compact observational context or provide "
                         "a separately validated larger allocation; no evidence was truncated.")


def fixed_context():
    """Opt-in fixed context allocation above the campaign's 32K ceiling.

    `SOLVER_FIXED_CONTEXT=65536` lifts the ceiling AND pins every request to that
    one allocation. Pinned, not merely raised: Ollama reloads the whole model
    whenever num_ctx changes, and adaptive 16K/32K switching was measured at
    ~32 s of reload per switch (CAMPAIGN_PERFORMANCE.md).

    The 32K ceiling was sized for gpt-oss:20b, whose sliding-window layers make
    context cheap (~24 KB/token). It is not a property of the problem: prompts
    for large functions exceed it and are refused before inference. Unset keeps
    the historical behaviour exactly. The value lands in every budget receipt
    because context size is the variable under test, never a silent knob.
    """
    raw = os.environ.get('SOLVER_FIXED_CONTEXT', '').strip()
    if not raw:
        return None
    value = int(raw)
    if not 8192 <= value <= 131072 or value & (value - 1):
        raise ValueError('SOLVER_FIXED_CONTEXT must be a power of two from 8192 to 131072')
    return value


def context_budget(prompt, num_predict, *, num_ctx=None, prefill='', response_schema=None):
    """Estimate conservatively for assembly/JSON; never claim tokenizer proof.

    Live assembly/JSON reached 27,850 tokens for 72,306 characters, invalidating
    the former chars/3 estimate. UTF-8 bytes/2 plus a template reserve errs higher
    on those workloads, but unusual text can still tokenize more densely. The
    response's actual prompt count is retained separately by the client.
    """
    if isinstance(num_predict, bool) or not isinstance(num_predict, int) or num_predict < 1:
        raise ValueError('num_predict must be a positive bounded output allowance')
    fixed = fixed_context()
    ceiling = fixed or 32768
    if num_ctx is not None and (isinstance(num_ctx, bool) or not isinstance(num_ctx, int)
                                or not 8192 <= num_ctx <= ceiling):
        raise ValueError(f'num_ctx must be an integer between 8192 and {ceiling}')
    # Schema normally constrains sampling rather than prompt tokens. Counting
    # its bytes is deliberately conservative across provider template variants.
    text_bytes = len(prompt.encode()) + len(prefill.encode())
    if response_schema is not None:
        text_bytes += len(json.dumps(response_schema, ensure_ascii=False).encode())
    estimated = (text_bytes + 1) // 2
    required = estimated + num_predict + 1024
    budget = {'version': 1, 'method': 'utf8-bytes/2-plus-template-reserve',
              'is_exact_token_count': False, 'estimated_prompt_tokens': estimated,
              'template_reserve_tokens': 1024, 'output_tokens': num_predict,
              'required_tokens': required, 'capacity': num_ctx or ceiling}
    if fixed:
        budget['fixed_context'] = fixed
    if required > budget['capacity']:
        raise ContextBudgetError(budget)
    budget['capacity'] = num_ctx or fixed or max(8192, 1 << (required - 1).bit_length())
    return budget
