"""Investigation actions share the real repair boundary, with independent budgets."""
import hashlib
import json

import pytest

from solver import prompt_budget, toolagent, workspace
from solver.experiment_memory import Notebook


class Provider:
    provider_id = 'scripted-investigation'

    def __init__(self, actions):
        self.actions = iter(actions)
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return json.dumps(next(self.actions)), {'eval_count': 5}


class BudgetProvider(Provider):
    def generate(self, request):
        prompt_budget.context_budget(request.prompt, request.num_predict,
            prefill=request.prefill, response_schema=request.response_schema)
        return super().generate(request)


def attempt(score=90, exact=False):
    return workspace.Attempt(True, score, exact, '-li v0,1\n+li v0,0', '', '')


@pytest.fixture
def environment(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, 'target_asm', lambda *_: 'glabel f\njr ra\nnop')
    monkeypatch.setattr(workspace, 'assert_uncontaminated', lambda *_: None)
    monkeypatch.setattr(workspace, 'score', lambda *_, **__: attempt(100, True))
    return tmp_path


def run(root, provider, **kwargs):
    return toolagent.search(root, 'f', 'int f(void) { return 0; }', root / 'ws',
        model='scripted', endpoint='http://unused', provider=provider,
        base_attempt=attempt(), **kwargs)


def test_phase_inspection_then_full_rewrite_without_open_book(environment):
    received = []
    def phase(action, candidate):
        received.append((action.hypothesis, candidate.source))
        return json.dumps({'status': 'observed', 'comparable': True, 'pre_as1': 'li v0,0'})
    provider = Provider([
        {'action': 'inspect_compiler', 'hypothesis': 'the pre-as1 constant is already wrong'},
        {'action': 'replace_source', 'hypothesis': 'reconstruct the return expression',
         'source': 'int f(void) { int x = 1; return x; }'},
    ])
    result = run(environment, provider, max_calls=4, max_compiles=3,
                 allow_reconstruction=True, investigation_tools={'inspect_compiler': phase})
    assert result.exact and result.compiles == 3
    assert received == [('the pre-as1 constant is already wrong', 'int f(void) { return 0; }')]
    assert 'li v0,0' in provider.requests[1].prompt
    assert 'complete-file replacement' not in provider.requests[1].prompt


def test_reconstruction_does_not_grant_source_read_tools(environment):
    provider = Provider([{'action': 'read_path', 'root': 'target', 'path': 'src/f.c'}])
    result = run(environment, provider, max_calls=1, allow_reconstruction=True)
    assert result.events[0]['status'] == 'unavailable-tool'


@pytest.mark.parametrize('source', [
    '#include "../../src/never_read_reference.c"\n',
    '#/**/include "../../src/never_read_reference.c"\n',
    '#inc\\\nlude "../../src/never_read_reference.c"\n',
    '#define HEADER "../../src/never_read_reference.c"\n#include HEADER\n',
    '%:include "../../src/never_read_reference.c"\n',
    '??=include "../../src/never_read_reference.c"\n',
    'GLOBAL_ASM("never_read_target.s");',
    'int f(void) { __asm__("li v0,1"); }',
    'int f(void) { asm volatile("li v0,1"); }',
    '\v#define A __a##sm__\nint f(void) { A("li v0,1"); }',
    '\f#define A __a##sm__\nint f(void) { A("li v0,1"); }',
])
def test_reconstruction_cannot_bypass_c_boundary(environment, monkeypatch, source):
    def compile_forbidden(*args, **kwargs):
        pytest.fail('source escape reached the compiler')
    monkeypatch.setattr(workspace, 'score', compile_forbidden)
    provider = Provider([{'action': 'replace_source', 'hypothesis': 'test reconstruction',
                          'source': source}])
    result = run(environment, provider, max_calls=1, allow_reconstruction=True,
                 require_inspection_before_patch=False)
    assert result.compiles == 0 and result.events[0]['status'] == 'invalid'


@pytest.mark.parametrize('context', ['#include "common.h"\n', '#pragma pack(4)\n'])
def test_reconstruction_preserves_admitted_include_context(environment, context):
    provider = Provider([{'action': 'replace_source', 'hypothesis': 'new body in same context',
        'source': context + 'int f(void) { return 1; }'}])
    result = toolagent.search(environment, 'f', context + 'int f(void) { return 0; }',
        environment / 'ws', model='scripted', endpoint='http://unused', provider=provider,
        base_attempt=attempt(), max_calls=1, allow_reconstruction=True,
        require_inspection_before_patch=False)
    assert result.compiles == 1 and result.exact


def test_phase_reserves_two_compiles_but_keeps_inspection_available(environment):
    def phase(*_):
        pytest.fail('phase must not launch with only one compile unit')
    provider = Provider([
        {'action': 'inspect_compiler', 'hypothesis': 'observe phase'},
        {'action': 'inspect_diff', 'view': 'first'},
    ])
    result = run(environment, provider, max_calls=2, max_compiles=1,
                 investigation_tools={'inspect_compiler': phase})
    assert result.events[0]['status'] == 'compile-budget-exhausted'
    assert result.events[1]['status'] == 'valid'
    assert result.compiles == 0 and result.tool_actions == 1


def test_failed_patch_retrieved_on_next_visit(environment, monkeypatch):
    notebook = Notebook(environment / 'history.jsonl', 'f', {'compiler': 'ido', 'target': 'a'})
    monkeypatch.setattr(workspace, 'score', lambda *_, **__: attempt(80))
    provider = Provider([
        {'action': 'inspect_diff', 'view': 'first'},
        {'action': 'patch', 'kind': 'expression', 'hypothesis': 'constant swap changes scheduling',
         'edits': [{'old': 'return 0;', 'new': 'return 2;'}]},
    ])
    result = run(environment, provider, max_calls=2, notebook=notebook)
    assert not result.exact and result.best.source == 'int f(void) { return 0; }'
    events = notebook.retrieve()['events']
    event = next(e for e in events if e['action'] == 'patch')
    assert event['before']['score'] == 90 and event['after']['score'] == 80
    assert event['parent_source_sha256'] != event['child_source_sha256']
    again = Provider([{'action': 'inspect_history'}])
    run(environment, again, max_calls=1, notebook=Notebook(environment / 'history.jsonl', 'f',
                                                        {'compiler': 'ido', 'target': 'a'}))
    assert 'constant swap changes scheduling' in again.requests[0].prompt
    assert '80' in again.requests[0].prompt


def test_execution_payload_reaches_active_candidate_without_compiling(environment):
    observed = []
    def execute(action, candidate):
        observed.append((action.payload, candidate.source))
        return json.dumps({'status': 'observed_failure', 'feedback': 'return differs for a0=9'})
    provider = Provider([{'action': 'execution_case', 'hypothesis': 'exercise nonzero argument',
                         'inputs': {'entry_registers': [['a0', 9]]}}])
    result = run(environment, provider, max_calls=1, max_compiles=0,
                 investigation_tools={'execution_case': execute})
    assert observed[0][0] == {'entry_registers': [['a0', 9]]}
    assert result.compiles == 0 and result.events[0]['status'] == 'valid'


def test_wall_budget_stops_before_another_request(environment, monkeypatch):
    ticks = iter([0.0, 2.0])
    monkeypatch.setattr(toolagent.time, 'monotonic', lambda: next(ticks))
    provider = Provider([])
    result = run(environment, provider, max_calls=12, max_seconds=1)
    assert result.calls_attempted == 0
    assert result.events[-1]['status'] == 'wall-budget-exhausted'


def test_expired_model_response_does_not_launch_a_tool(environment, monkeypatch):
    ticks = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(toolagent.time, 'monotonic', lambda: next(ticks))
    provider = Provider([{'action': 'inspect_compiler', 'hypothesis': 'observe phase'}])
    result = run(environment, provider, max_calls=1, max_seconds=1,
                 investigation_tools={'inspect_compiler': lambda *_: pytest.fail('deadline expired')})
    assert result.events[-1]['status'] == 'wall-budget-exhausted'
    assert result.compiles == 0


def test_capability_request_has_bounded_json_payload():
    action = toolagent.parse_action(json.dumps({'action': 'request_capability',
        'hypothesis': 'one shared parser obstruction', 'task': {'issue_key': 'abc'}}))
    assert action.payload == {'issue_key': 'abc'}
    with pytest.raises(ValueError, match='payload'):
        toolagent.parse_action(json.dumps({'action': 'execution_case', 'hypothesis': 'test', 'inputs': 'code'}))


def test_campaign_binds_investigation_budgets_and_memory(tmp_path, monkeypatch):
    from eval import completion_campaign as campaign, agentrepair
    from solver import investigation
    source = 'int f(void) { return 0; }'
    path = tmp_path / 'candidate.c'
    path.write_text(source)
    bound = {}
    def repair(**kwargs):
        bound.update(kwargs)
        return {'result': {'best_attempt_id': 2, 'best_source_path': str(path),
            'best_source_sha256': hashlib.sha256(source.encode()).hexdigest(),
            'best_residual': {'weighted_progress_score': 90}}}
    monkeypatch.setattr(agentrepair, 'run', repair)
    node = {'source': str(path), 'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
            'attempt_id': 1, 'residual': {}, 'shared_evidence_sha256': 'evidence',
            'data_evidence_sha256': 'binary-data'}
    profile = {'name': 'investigate', 'model': True, 'investigate': True,
               'deterministic_budget': 0, 'investigation_policy': investigation.policy()}
    config = {'model': 'local', 'endpoint': 'http://unused', 'model_calls': 3,
              'timeout': 240, 'num_predict': 6000, 'project': str(tmp_path),
              'experiment_memory_root': str(tmp_path / 'notebooks')}
    campaign.execute(repo=tmp_path, db=tmp_path / 'test.db', function='f', node=node,
                     profile=profile, config=config, out=tmp_path / 'repair.json')
    assert bound['max_calls'] == 12
    assert bound['investigation_policy']['compiles'] == 16
    assert bound['experiment_memory'] == tmp_path / 'notebooks/f.jsonl'
    assert bound['investigation_identity']['shared_evidence'] == 'evidence'
    assert bound['investigation_identity']['binary_data'] == 'binary-data'


def test_additional_cases_preserve_base_execution_debt(tmp_path):
    from solver.investigation import Tools
    from solver.modelrepair import CandidateState
    tools = Tools(tmp_path, tmp_path, None, 'f', tmp_path)
    class Extra:
        cases = [1]
        def evaluate(self, *_):
            return {'status': 'finite_case_pass', 'receipt': 'case.json', 'cases': [], 'scope': 'finite'}
    tools.execution = Extra()
    state = CandidateState('int f(void) {return 0;}', attempt(), None)
    base = {'status': 'observed_pass_with_execution_debt', 'reason': 'opaque callee'}
    result = tools.evaluate(state, lambda _: base)
    assert result['status'] == base['status'] and result['reason'] == base['reason']
    assert 'investigation_cases' in result and 'investigation_cases' not in base


def test_advanced_history_compacts_before_context_budget_is_exhausted(environment):
    actions = [
        {'action': 'execution_case', 'hypothesis': f'exercise path {i}',
         'inputs': {'entry_registers': [['a1', i]]}}
        for i in range(10)
    ] + [{'action': 'finish', 'reason': 'bounded observations collected'}]
    provider = BudgetProvider(actions)
    notebook = Notebook(environment / 'long-history.jsonl', 'f',
                        {'compiler': 'ido', 'target': 'a'})
    result = run(environment, provider, max_calls=len(actions), num_predict=6000,
                 allow_reconstruction=True, notebook=notebook,
                 investigation_tools={'execution_case': lambda *_: 'O' * 10000})
    assert result.events[-1]['action'] == 'finish'
    assert not any(event['status'] == 'generation-error' for event in result.events)
    assert len(provider.requests) == len(actions)
    assert 'O' * 6000 not in provider.requests[-1].prompt
    assert 'int f(void) { return 0; }' in provider.requests[-1].prompt
    assert 'TARGET ASSEMBLY:' in provider.requests[-1].prompt
    assert 'ACTIVE RESIDUAL:' in provider.requests[-1].prompt
    assert all(len(event.get('observation', '')) == 6000
               for event in result.events if event['action'] == 'execution_case')
    assert len(notebook.retrieve(limit=20)['events']) >= 10


def test_irreducible_advanced_source_stops_once_with_budget_details(environment):
    provider = BudgetProvider([])
    source = 'int f(void) { /* ' + 'S' * 130000 + ' */ return 0; }'
    result = toolagent.search(environment, 'f', source, environment / 'ws',
        model='scripted', endpoint='http://unused', provider=provider,
        base_attempt=attempt(), max_calls=4, num_predict=6000,
        allow_reconstruction=True)
    assert provider.requests == []
    assert len(result.events) == 1
    assert result.events[0]['status'] == 'context-budget-exhausted'
    budget = result.events[0]['context_budget']
    assert budget['required_tokens'] > budget['capacity']
    assert budget['output_tokens'] == 6000
    assert result.events[0]['step'] == 1


def test_compact_history_keeps_measured_outcomes_and_candidate_lineage():
    from solver.investigation_prompt import history_view
    event = {'action': 'patch', 'status': 'valid', 'active_candidate': 'c0',
             'child_candidate': 'c1', 'hypothesis': 'fewer temporaries',
             'compiled': True, 'exact': False, 'score': 45,
             'residual': {'faults': {'structural': 4}, 'first_difference': ['-lw v0,0(a0)'],
                          'frontend': {'recipe': 'large unrelated detail'}}}
    view = json.loads(history_view([event], 4000))
    assert view['score'] == 45 and view['child_candidate'] == 'c1'
    assert view['residual']['faults'] == {'structural': 4}
    assert 'frontend' not in view['residual']
    assert 'frontend' in event['residual']


def test_classical_provider_context_budget_failure_is_not_retried(environment):
    provider = BudgetProvider([])
    source = 'int f(void) { /* ' + 'S' * 130000 + ' */ return 0; }'
    result = toolagent.search(environment, 'f', source, environment / 'ws',
        model='scripted', endpoint='http://unused', provider=provider,
        base_attempt=attempt(), max_calls=4, num_predict=6000)
    assert len(result.events) == 1
    assert result.events[0]['status'] == 'context-budget-exhausted'
    assert result.events[0]['context_budget']['required_tokens'] > 32768


def test_new_investigation_policy_has_own_bounded_visits():
    from solver import investigation, repair_queue
    from eval import completion_campaign as campaign
    n = {'status': 'pending', 'source_sha256': 'parent',
         'residual': {'compiled': True, 'frontend': {'passed': True}},
         'semantic_validation': {'source_sha256': 'parent', 'status': 'unavailable'},
         'jobs': [{'profile': 'investigate', 'evidence_key': str(i)} for i in range(3)]}
    selected = investigation.profile(n, 3, campaign.PROFILES, investigation.policy())
    assert selected['name'] == 'investigate'
    campaign.accept(n, selected, {'semantic_validation': n['semantic_validation']}, __import__('pathlib').Path('receipt'))
    later = investigation.profile(n, 3, campaign.PROFILES, investigation.policy())
    assert later is None or later['name'] != 'investigate'


def test_capability_catalog_is_an_allowlisted_inspection_action():
    action = toolagent.parse_action('{"action":"inspect_capabilities","query":"parser"}')
    assert action.query == 'parser' and action.name in toolagent.INSPECTION_ACTIONS


def test_advanced_actions_are_front_loaded_and_repeated_lookup_is_masked(environment):
    provider = Provider([{'action': 'inspect_definition', 'query': 'NoSuchType'}] * 3 +
                        [{'action': 'inspect_diff', 'view': 'first'}])
    run(environment, provider, max_calls=4, allow_reconstruction=True,
        investigation_tools={'inspect_compiler': lambda *_: '{}'})
    first = provider.requests[0]
    assert first.prompt.index('inspect_compiler') < first.prompt.index('TARGET ASSEMBLY')
    assert first.response_schema is not None
    assert first.prefill == ''  # A complete-object grammar must own its opening brace.
    last = provider.requests[-1]
    allowed = [branch['properties']['action']['enum'][0] for branch in last.response_schema['anyOf']]
    assert 'inspect_definition' not in allowed
    assert 'replace_source' in allowed and 'read_path' not in allowed
    replacement = next(branch for branch in last.response_schema['anyOf']
                       if branch['properties']['action']['enum'] == ['replace_source'])
    assert set(replacement['required']) == {'action', 'hypothesis', 'source'}
    assert replacement['properties']['source']['minLength'] == 1
