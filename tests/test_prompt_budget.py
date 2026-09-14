import copy
import json

import pytest

from solver import llm, prompt_budget


def test_pass_projection_preserves_debt_bindings_unknown_fields_and_provenance():
    report = {'status': 'observed_pass_with_execution_debt', 'source_sha256': 'source',
        'panel_sha256': 'panel', 'authoritative': False, 'debt': ['finite inputs', 'opaque call'],
        'call_contracts': {'f': {'arguments': [1, 2]}},
        'callee_environment': {'leaves': {'f': {'authority': 'ROM', 'program': [1, 2]}}},
        'new_binding_obligation': {'do_not_drop': [1, 2, 3]},
        'source_object_obligations': [{'expected': 'u64'}],
        'target_coverage': {'status': 'partial', 'covered_instruction_count': 1,
            'missing_instructions': [{'instruction': 9}], 'unresolved_indirect_jumps': ['unknown']},
        'stress_work': {'stop_reasons': ['budget'], 'selected_cases': [{'registers': [0]}],
            'target_coverage': {'missing_instructions': [1, 2]}},
        'exploration_trials': {'scope': 'discarded prefixes', 'noncompleted_examples': [1]},
        'passed_case_indices': [0, 1], 'feedback': []}
    original = copy.deepcopy(report)
    result = prompt_budget.semantic_summary(report)
    assert report == original
    for field in ('debt', 'call_contracts', 'callee_environment', 'new_binding_obligation',
                  'source_object_obligations', 'source_sha256', 'panel_sha256', 'authoritative'):
        assert result[field] == report[field]
    assert result['target_coverage']['unresolved_indirect_jumps'] == ['unknown']
    assert result['_prompt_projection']['omitted_observation_counts']['stress_work.target_coverage.missing_instructions'] == 2
    assert result == prompt_budget.semantic_summary(report)
    changed = copy.deepcopy(report)
    changed['stress_work']['selected_cases'][0]['registers'] = [1]
    assert prompt_budget.semantic_summary(changed)['_prompt_projection']['full_report_sha256'] != result['_prompt_projection']['full_report_sha256']


@pytest.mark.parametrize('report', [
    {'status': 'observed_failure', 'feedback': ['wrong result'], 'stress_work': {'selected_cases': [1]}},
    {'status': 'unavailable', 'reason': 'unknown'},
    {'status': 'observed_pass', 'feedback': ['unexpected disagreement']},
    {'status': 'observed_pass', 'outcome_accounting': {'observed_disagreements': 1}},
])
def test_failure_unknown_or_contradictory_report_is_not_compacted(report):
    assert prompt_budget.semantic_summary(report) == report


def test_overflow_refuses_before_request_and_includes_prefill_schema(monkeypatch):
    monkeypatch.setattr(llm.urllib.request, 'urlopen', lambda *a, **k: pytest.fail('sent overflowing request'))
    with pytest.raises(prompt_budget.ContextBudgetError) as err:
        llm.generate('unused', 'gpt-oss:20b', 'x' * 72306, num_predict=6000, num_ctx=32768)
    assert err.value.context_budget['required_tokens'] > 32768
    with pytest.raises(prompt_budget.ContextBudgetError):
        llm.generate('unused', 'model', 'small', prefill='x' * 60000, num_ctx=32768)
    with pytest.raises(prompt_budget.ContextBudgetError):
        llm.generate('unused', 'model', 'small', response_schema={'description': 'x' * 60000}, num_ctx=32768)
    # Count bytes, not code points; non-ASCII content must not appear cheaper.
    assert prompt_budget.context_budget('漢' * 100, 100)['estimated_prompt_tokens'] == 150


def test_actual_headroom_diagnostics_are_recorded_without_altering_request(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self): return json.dumps({'response': 'answer', 'prompt_eval_count': 30000}).encode()
    sent = []
    def send(request, timeout):
        sent.append(json.loads(request.data))
        return Response()
    monkeypatch.setattr(llm.urllib.request, 'urlopen', send)
    text, meta = llm.generate('http://local', 'model', 'short', num_predict=6000, num_ctx=32768)
    assert text == 'answer'
    assert sent[0]['options']['num_predict'] == 6000
    assert meta['_context_budget']['actual_output_headroom'] == 2768
    assert meta['_context_budget']['actual_allowance_fits'] is False
    assert meta['_context_budget']['is_exact_token_count'] is False


def test_fixed_context_lets_a_large_function_prompt_through_that_32k_refuses(monkeypatch):
    """Fires on its motivating residual: a prompt the 32K ceiling rejects.

    ~27.9K prompt tokens plus a 6K reasoning allowance needs ~35K, which the
    campaign ceiling refuses before inference -- the large-function case.
    """
    prompt = 'x' * 55800
    monkeypatch.delenv('SOLVER_FIXED_CONTEXT', raising=False)
    with pytest.raises(prompt_budget.ContextBudgetError):
        prompt_budget.context_budget(prompt, 6000)
    monkeypatch.setenv('SOLVER_FIXED_CONTEXT', '65536')
    budget = prompt_budget.context_budget(prompt, 6000)
    assert budget['required_tokens'] > 32768
    assert budget['capacity'] == 65536 and budget['fixed_context'] == 65536


def test_fixed_context_pins_one_allocation_for_small_prompts_too(monkeypatch):
    # Pinned, not adaptive: a changing num_ctx reloads the whole Ollama model.
    monkeypatch.setenv('SOLVER_FIXED_CONTEXT', '65536')
    assert prompt_budget.context_budget('small', 100)['capacity'] == 65536


def test_unset_fixed_context_keeps_the_historical_ceiling_exactly(monkeypatch):
    monkeypatch.delenv('SOLVER_FIXED_CONTEXT', raising=False)
    budget = prompt_budget.context_budget('small', 100)
    assert budget['capacity'] == 8192 and 'fixed_context' not in budget
    with pytest.raises(ValueError, match='between 8192 and 32768'):
        prompt_budget.context_budget('small', 100, num_ctx=65536)


@pytest.mark.parametrize('value', ['70000', '4096', '262144', 'lots'])
def test_fixed_context_rejects_values_that_are_not_valid_allocations(monkeypatch, value):
    monkeypatch.setenv('SOLVER_FIXED_CONTEXT', value)
    with pytest.raises(ValueError):
        prompt_budget.context_budget('small', 100)
