"""Auditable prompt compaction and conservative, explicitly estimated headroom.

Full semantic receipts remain authoritative. Only known verbose observation
enumerations are elided; unknown fields and all binding obligations survive.
"""
import copy
import hashlib
import json


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


def context_budget(prompt, num_predict, *, num_ctx=None, prefill='', response_schema=None):
    """Estimate conservatively for assembly/JSON; never claim tokenizer proof.

    Live assembly/JSON reached 27,850 tokens for 72,306 characters, invalidating
    the former chars/3 estimate. UTF-8 bytes/2 plus a template reserve errs higher
    on those workloads, but unusual text can still tokenize more densely. The
    response's actual prompt count is retained separately by the client.
    """
    if isinstance(num_predict, bool) or not isinstance(num_predict, int) or num_predict < 1:
        raise ValueError('num_predict must be a positive bounded output allowance')
    if num_ctx is not None and (isinstance(num_ctx, bool) or not isinstance(num_ctx, int)
                                or not 8192 <= num_ctx <= 32768):
        raise ValueError('num_ctx must be an integer between 8192 and 32768')
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
              'required_tokens': required, 'capacity': num_ctx or 32768}
    if required > budget['capacity']:
        raise ContextBudgetError(budget)
    budget['capacity'] = num_ctx or max(8192, 1 << (required - 1).bit_length())
    return budget
