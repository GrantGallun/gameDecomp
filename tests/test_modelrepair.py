"""The local model may propose edits; only bounded, verified children advance."""

from pathlib import Path

import json

import pytest

from solver import modelrepair, workspace


def _attempt(score=90.0, *, exact=False, diff="- target\n+ current"):
    return workspace.Attempt(True, score, exact, diff, "", "")


def test_object_exact_frontend_failure_still_calls_model(monkeypatch, tmp_path):
    root = _attempt(100, exact=True, diff='')
    root.frontend = {'passed': False, 'status': 'rejected', 'diagnostics': 'wrong callback type'}
    child = _attempt(100, exact=True, diff='')
    child.frontend = {'passed': True, 'status': 'passed'}
    seen = []
    class Provider:
        provider_id = 'test'
        def generate(self, request):
            seen.append(request.prompt)
            return (json.dumps({'kind': 'call-arguments', 'hypothesis': 'Use the required callback type',
                'edits': [{'old': 'work(cb)', 'new': 'work((Callback)cb)'}]}), {'done_reason': 'stop'})
    monkeypatch.setattr(workspace, 'target_asm', lambda *a: 'glabel f\njr ra\nnop')
    monkeypatch.setattr(workspace, 'assert_uncontaminated', lambda *a: None)
    monkeypatch.setattr(workspace, 'score', lambda *a, **kw: child)
    result = modelrepair.search(tmp_path, 'f', 'void f(void) { work(cb); }', tmp_path,
        model='test', endpoint='unused', base_attempt=root, max_calls=1, provider=Provider())
    assert result.exact and result.calls_attempted == 1
    assert 'wrong callback type' in seen[0]
    assert 'PROJECT C FRONTEND REJECTED' in seen[0]
    assert '(Callback)cb' in result.best_source
    assert root.exact  # object evidence is not relabeled as a byte mismatch


def test_noncompiling_frontier_retains_better_context():
    failed = workspace.Attempt(False, 0, False, '', 'syntax error', '')
    raw = modelrepair.CandidateState('void f(? x) {}', failed)
    contextual = modelrepair.CandidateState('#include "task.h"\nvoid f(Task *x) {}', failed)
    assert modelrepair._frontier([raw, contextual], 2) == [contextual, raw]


def test_real_compile_errors_outrank_header_count_heuristic():
    broken = workspace.Attempt(False, 0, False, '', 'error', '', frontend={
        'passed':False, 'diagnostics':"include/shim.h:1:1: error: conflicting type\ncandidate.c:2:1: error: sp undefined"})
    clean = workspace.Attempt(False, 0, False, '', 'error', '', frontend={
        'passed':False, 'diagnostics':"candidate.c:2:1: error: sp undefined"})
    a = modelrepair.CandidateState('#include "a.h"\n#include "shim.h"\nvoid f(void) {}', broken)
    b = modelrepair.CandidateState('#include "a.h"\nvoid f(void) {}', clean)
    assert modelrepair._frontier([a,b], 2)[0] is b
    assert modelrepair._quality(clean) > modelrepair._quality(broken)


def test_frontend_pass_cannot_hide_ido_compile_failure():
    failed = workspace.Attempt(False, 0, False, '', '', '', frontend={'passed': True})
    assert modelrepair._quality(_attempt()) > modelrepair._quality(failed)


def test_loop_lowering_and_frontend_errors_survive_helper_diagnostic():
    guard = workspace.Attempt(False, 0, False, '', 'ERROR: The C file contains a do-while loop.', '',
        frontend={'passed':False, 'diagnostics':"candidate.c:3:2: error: incompatible array pointer"})
    lowered = workspace.Attempt(False, 0, False, '', 'type error', '', frontend=guard.frontend)
    assert modelrepair.compile_error_rank(lowered) < modelrepair.compile_error_rank(guard)
    assert 'incompatible array pointer' in modelrepair.compile_feedback(guard)


def test_compile_only_stops_at_frontend_pass_without_claiming_exact(monkeypatch, tmp_path):
    class Provider:
        provider_id = 'test'
        def generate(self, request):
            return (json.dumps({'kind':'expression','hypothesis':'fix declaration use',
                'edits':[{'old':'broken','new':'working'}]}), {'done_reason':'stop'})
    monkeypatch.setattr(workspace, 'target_asm', lambda *a: 'glabel f\njr ra\nnop')
    monkeypatch.setattr(workspace, 'assert_uncontaminated', lambda *a: None)
    child = _attempt(70)
    child.frontend = {'passed': True}
    monkeypatch.setattr(workspace, 'score', lambda *a, **kw: child)
    result = modelrepair.search(tmp_path, 'f', 'void f(void) { broken(); }', tmp_path,
        model='test', endpoint='unused', base_attempt=workspace.Attempt(False,0,False,'','error',''),
        max_calls=6, max_depth=6, provider=Provider(), compile_only=True)
    assert result.calls_attempted == 1 and result.best_attempt.compiled and not result.exact


def test_reverified_retained_exact_candidate_terminates_without_model(tmp_path):
    state = modelrepair.CandidateState('int f(void) { return 2; }', _attempt(100, exact=True))
    result = modelrepair.search(tmp_path, 'f', 'int f(void) { return 1; }', tmp_path,
        model='unused', endpoint='unused', base_attempt=_attempt(),
        initial_states=(state,), max_calls=0)
    assert result.exact
    assert result.best_source == state.source
    assert result.calls_attempted == 0


@pytest.mark.parametrize('early_guidance', [False, True])
def test_strategy_brief_and_schema_reach_provider(monkeypatch, tmp_path, early_guidance):
    observed = []

    class Provider:
        provider_id = "test"

        def generate(self, request):
            observed.append(request)
            return ('{"kind":"expression","hypothesis":"test constant",'
                    '"edits":[{"old":"return 1;","new":"return 2;"}]}',
                    {"done_reason": "stop", "eval_count": 5})

    monkeypatch.setattr(workspace, "target_asm", lambda *a: "glabel f\njr ra\nnop")
    monkeypatch.setattr(workspace, "assert_uncontaminated", lambda *a: None)
    monkeypatch.setattr(workspace, "score", lambda *a, **kw: _attempt(100, exact=True))
    result = modelrepair.search(tmp_path, "f", "int f(void) { return 1; }", tmp_path,
        model="test", endpoint="unused", base_attempt=_attempt(), max_calls=1,
        strategy_brief="Try the observed constant mismatch.", structured_output=True, provider=Provider(),
        early_patch_guidance=early_guidance)
    assert result.exact
    assert observed[0].response_schema == modelrepair.EDIT_SCHEMA
    assert "Try the observed constant mismatch." in observed[0].prompt
    assert observed[0].prompt.startswith('PATCH ADDRESSING') is early_guidance


def test_semantic_prompt_preserves_strategy_and_accepts_condition_operand_edit(monkeypatch,tmp_path):
    from solver import compile_obligations
    observed=[]
    source='int f(int x) { if (x == 1) return 1; return 0; }'
    class Provider:
        provider_id='test'
        def generate(self,request):
            observed.append(request.prompt)
            return (json.dumps({'kind':'expression','hypothesis':'correct compared value',
                'edits':[{'old':'x == 1','new':'x == 2'}]}),{'done_reason':'stop'})
    monkeypatch.setattr(workspace,'target_asm',lambda *a:'glabel f\njr ra\nnop')
    monkeypatch.setattr(workspace,'assert_uncontaminated',lambda *a:None)
    monkeypatch.setattr(workspace,'score',lambda *a,**k:_attempt(100,exact=True))
    monkeypatch.setattr(compile_obligations,'header_types',lambda *a,**k:{})
    result=modelrepair.search(tmp_path,'f',source,tmp_path,model='test',endpoint='unused',
        base_attempt=_attempt(),max_calls=1,provider=Provider(),resilient=True,
        semantic_evaluator=lambda state:{'status':'observed_failure','counts':{'failed':1},'feedback':[]},
        strategy_brief='Use the verified control dependency, not a guessed input constant.')
    assert result.best_source==source.replace('x == 1','x == 2')
    assert observed[0].count('Use the verified control dependency')==1
    assert 'including a condition operand' in observed[0]
    assert 'not its enclosing if header' not in observed[0]
    assert 'EDITABLE SOURCE SLOTS' not in observed[0]


def test_invalid_patch_gets_one_source_bound_correction_within_budget(monkeypatch, tmp_path):
    observed = []

    class Provider:
        provider_id = "test"

        def generate(self, request):
            observed.append(request)
            old = "return 9;" if len(observed) == 1 else "return 1;"
            return ('{"kind":"expression","hypothesis":"test constant",'
                    '"edits":[{"old":"' + old + '","new":"return 2;"}]}', {"done_reason": "stop"})

    monkeypatch.setattr(workspace, "target_asm", lambda *a: "glabel f\njr ra\nnop")
    monkeypatch.setattr(workspace, "assert_uncontaminated", lambda *a: None)
    monkeypatch.setattr(workspace, "score", lambda *a, **kw: _attempt(100, exact=True))
    result = modelrepair.search(tmp_path, "f", "int f(void) { return 1; }", tmp_path,
        model="test", endpoint="unused", base_attempt=_attempt(), max_calls=2,
        retry_invalid=True, provider=Provider())
    assert result.exact and result.calls_attempted == 2 and result.invalid_proposals == 1
    assert "PATCH APPLICATION FAILED" in observed[1].prompt
    assert "old substring occurs 0 times" in observed[1].prompt
    assert "return 9;" in observed[1].prompt
    assert observed[1].response_schema == modelrepair.EDIT_SCHEMA


def test_invalid_correction_cannot_expand_call_budget(monkeypatch, tmp_path):
    observed = []

    class Provider:
        provider_id = "test"

        def generate(self, request):
            observed.append(request)
            return ('{"kind":"expression","hypothesis":"bad",'
                    '"edits":[{"old":"absent;","new":"return 2;"}]}', {"done_reason": "stop"})

    monkeypatch.setattr(workspace, "target_asm", lambda *a: "glabel f\njr ra\nnop")
    monkeypatch.setattr(workspace, "assert_uncontaminated", lambda *a: None)
    result = modelrepair.search(tmp_path, "f", "int f(void) { return 1; }", tmp_path,
        model="test", endpoint="unused", base_attempt=_attempt(), max_calls=1,
        retry_invalid=True, provider=Provider())
    assert not result.exact and len(observed) == 1


def test_parse_structured_edit_from_noisy_response():
    proposal = modelrepair.parse_proposal(
        'reasoning first\n```json\n'
        '{"kind":"expression","hypothesis":"wrong constant",'
        '"edits":[{"old":"return 1;","new":"return 2;"}]}'
        '\n```')

    assert proposal.kind == "expression"
    assert proposal.edits[0].old == "return 1;"


def test_parse_can_salvage_valid_edits_from_verbose_hypothesis():
    response = (
        '{"kind":"expression","hypothesis":"' + ("reason " * 80) + '",'
        '"edits":[{"old":"return 1;","new":"return 2;"}]}'
    )
    with pytest.raises(ValueError, match="hypothesis too long"):
        modelrepair.parse_proposal(response)

    proposal = modelrepair.parse_proposal(
        response, truncate_hypothesis=True)

    assert len(proposal.hypothesis) == 400
    assert proposal.edits == (
        modelrepair.Edit("return 1;", "return 2;"),)

    unknown_kind = response.replace('"kind":"expression"',
                                    '"kind":"remove-dead-code"')
    with pytest.raises(ValueError, match="unknown repair kind"):
        modelrepair.parse_proposal(
            unknown_kind, truncate_hypothesis=True)
    normalized = modelrepair.parse_proposal(
        unknown_kind, truncate_hypothesis=True, normalize_kind=True)
    assert normalized.kind == "other"

    missing_hypothesis = unknown_kind.replace(
        '"hypothesis":"' + ("reason " * 80) + '",', "")
    with pytest.raises(ValueError, match="missing hypothesis"):
        modelrepair.parse_proposal(
            missing_hypothesis, normalize_kind=True)
    defaulted = modelrepair.parse_proposal(
        missing_hypothesis, normalize_kind=True, default_hypothesis=True)
    assert defaulted.hypothesis == "model-proposed bounded repair"


def test_apply_requires_unique_substrings_and_rejects_whole_file():
    ambiguous = modelrepair.Proposal(
        "expression", "change x", (modelrepair.Edit("x", "y"),))
    with pytest.raises(ValueError, match="occurs 2 times"):
        modelrepair.apply_proposal("x + x", ambiguous)
    # The correction names where the copies are, so it can disambiguate them.
    with pytest.raises(ValueError, match=r"lines 2, 4\); extend each old span"):
        modelrepair.apply_proposal("int a;\nf(x, 1);\n\nf(x, 2);\n", ambiguous)

    source = "int f(void) { return 1; }"
    whole = modelrepair.Proposal(
        "expression", "rewrite", (modelrepair.Edit(source, "other"),))
    with pytest.raises(ValueError, match="whole-file"):
        modelrepair.apply_proposal(source, whole)

    escape = modelrepair.Proposal(
        "other", "bypass", (modelrepair.Edit("return 1;", "asm(\"jr ra\");"),))
    with pytest.raises(ValueError, match="forbidden"):
        modelrepair.apply_proposal(source, escape)

    comment = modelrepair.Proposal(
        "layout", "move field", (modelrepair.Edit(
            "return 1;", "return 1; /* now at offset 4 */"),))
    with pytest.raises(ValueError, match="comments or whitespace"):
        modelrepair.apply_proposal(source, comment)

    literal = modelrepair.Proposal(
        "expression", "change text", (modelrepair.Edit(
            "return 1;", 'puts("a b"); return 1;'),))
    assert '"a b"' in modelrepair.apply_proposal(source, literal)


def test_existing_include_can_anchor_declarations_but_include_context_cannot_change():
    source = '#include "common.h"\nvoid f(void) {}'
    value = {"kind": "declarations", "hypothesis": "insert a declaration after the existing include",
             "edits": [{"old": '#include "common.h"',
                        "new": '#include "common.h"\nstruct Actor { int x; };'}]}
    proposal = modelrepair.parse_proposal(json.dumps(value))
    actual = modelrepair.apply_proposal(source, proposal)
    assert 'struct Actor { int x; };' in actual
    for replacement in ('#include "different.h"', '#include "common.h"\n#include "other.h"',
                        '#include "common.h"\nasm("nop");', '#pragma once'):
        value["edits"][0]["new"] = replacement
        with pytest.raises(ValueError, match="forbidden"):
            modelrepair.parse_proposal(json.dumps(value))
    # A substring edit inside an include cannot evade the per-edit line check.
    proposal = modelrepair.Proposal("other", "change header", (modelrepair.Edit("common.h", "other.h"),))
    with pytest.raises(ValueError, match="include context"):
        modelrepair.apply_proposal(source, proposal)


def test_apply_can_relax_unique_indentation_when_opted_in():
    source = (
        "void f(void) {\n"
        "    if (ready &&\n        enabled) {\n        run();\n    }\n"
        "}\n")
    proposal = modelrepair.Proposal(
        "control-flow", "use the right guard",
        (modelrepair.Edit(
            "if (ready &&\n    enabled) {\n    run();\n}",
            "if (sound == 0 &&\n    enabled) {\n    run();\n}"),))

    with pytest.raises(ValueError, match="old substring occurs 0 times"):
        modelrepair.apply_proposal(source, proposal)

    result = modelrepair.apply_proposal(
        source, proposal, relaxed_whitespace=True)
    assert result == (
        "void f(void) {\n"
        "    if (sound == 0 &&\n"
        "        enabled) {\n"
        "        run();\n"
        "    }\n"
        "}\n")


def test_relaxed_whitespace_still_rejects_ambiguous_edit():
    proposal = modelrepair.Proposal(
        "expression", "ambiguous", (modelrepair.Edit("x + y", "x - y"),))

    with pytest.raises(ValueError, match="relaxed form occurs 2 times"):
        modelrepair.apply_proposal(
            "x  + y;\nx\t+ y;\n", proposal, relaxed_whitespace=True)


def test_search_scores_sibling_proposals_and_keeps_oracle_exact(monkeypatch):
    responses = iter([
        ('{"kind":"expression","hypothesis":"try one",'
         '"edits":[{"old":"return 0;","new":"return 1;"}]}',
         {"eval_count": 10, "done_reason": "stop"}),
        ('{"kind":"expression","hypothesis":"try two",'
         '"edits":[{"old":"return 0;","new":"return 2;"}]}',
         {"eval_count": 11, "done_reason": "stop"}),
    ])
    monkeypatch.setattr(modelrepair.llm, "generate",
                        lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(modelrepair.workspace, "target_asm",
                        lambda _ws, _name: "jr ra")
    monkeypatch.setattr(modelrepair.workspace, "assert_uncontaminated",
                        lambda *_args, **_kwargs: None)

    def score(_ws, _repo, _name, source, **_kwargs):
        if "return 2;" in source:
            return _attempt(100.0, exact=True, diff="")
        return _attempt(95.0)

    monkeypatch.setattr(modelrepair.workspace, "score", score)
    source = "int f(void) { return 0; }"
    result = modelrepair.search(
        Path("repo"), "f", source, Path("ws"), model="local",
        endpoint="http://local", base_attempt=_attempt(), draws=2,
        max_depth=1, beam_width=2)

    assert result.exact
    assert "return 2;" in result.best_source
    assert result.generations == 2 and result.tokens == 21


def test_search_can_follow_successive_compile_errors(monkeypatch):
    responses = iter([
        ('{"kind":"declarations","hypothesis":"declare a",'
         '"edits":[{"old":"int f(void)","new":"int a;\\nint f(void)"}]}',
         {"eval_count": 5}),
        ('{"kind":"declarations","hypothesis":"declare b",'
         '"edits":[{"old":"int a;","new":"int a;\\nint b;"}]}',
         {"eval_count": 6}),
    ])
    monkeypatch.setattr(modelrepair.llm, "generate",
                        lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(modelrepair.workspace, "target_asm",
                        lambda _ws, _name: "jr ra")
    monkeypatch.setattr(modelrepair.workspace, "assert_uncontaminated",
                        lambda *_args, **_kwargs: None)

    prompts = []

    def score(_ws, _repo, _name, source, **kwargs):
        prompts.append(kwargs.get("prompt", ""))
        if "int b;" in source:
            return _attempt(100.0, exact=True, diff="")
        return workspace.Attempt(False, 0.0, False, "", "b undefined", "")

    monkeypatch.setattr(modelrepair.workspace, "score", score)
    base = workspace.Attempt(False, 0.0, False, "", "a undefined", "")
    result = modelrepair.search(
        Path("repo"), "f", "int f(void) { return a + b; }", Path("ws"),
        model="local", endpoint="http://local", base_attempt=base,
        draws=1, max_depth=2, beam_width=2)

    assert result.exact
    assert "int a;\nint b;" in result.best_source
    assert "b undefined" in prompts[-1]


def test_exhaust_budget_retries_parent_after_invalid_proposals(monkeypatch):
    monkeypatch.setattr(
        modelrepair.llm, "generate",
        lambda *_args, **_kwargs: ("not json", {"eval_count": 1}))
    monkeypatch.setattr(modelrepair.workspace, "target_asm",
                        lambda _ws, _name: "jr ra")
    monkeypatch.setattr(modelrepair.workspace, "assert_uncontaminated",
                        lambda *_args, **_kwargs: None)

    result = modelrepair.search(
        Path("repo"), "f", "int f(void) { return 0; }", Path("ws"),
        model="local", endpoint="http://local", base_attempt=_attempt(),
        draws=2, max_depth=2, beam_width=1, seed=10,
        exhaust_budget=True)

    assert result.calls_attempted == 4
    assert result.generations == 4
    assert result.tokens == result.charged_tokens == 4


def test_duplicate_proposal_is_told_what_that_source_already_did(monkeypatch):
    class Provider:
        provider_id = "test-provider"

        def __init__(self):
            self.prompts = []

        def generate(self, request):
            self.prompts.append(request.prompt)
            return ('{"kind":"expression","hypothesis":"fix constant",'
                    '"edits":[{"old":"return 0;","new":"return 1;"}]}', {"eval_count": 1})

    provider = Provider()
    monkeypatch.setattr(modelrepair.workspace, "target_asm", lambda _ws, _name: "glabel f\njr ra\nnop")
    monkeypatch.setattr(modelrepair.workspace, "assert_uncontaminated", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(modelrepair.workspace, "score", lambda *_args, **_kwargs: _attempt(95.0))

    result = modelrepair.search(
        Path("repo"), "f", "int f(void) { return 0; }", Path("ws"),
        model="local", endpoint="provider://test", provider=provider, base_attempt=_attempt(90.0),
        draws=2, max_depth=1, beam_width=1, max_calls=3, retry_invalid=True)

    duplicate = [line for line in result.log if "duplicate" in line]
    assert duplicate and "the depth-1 child" in duplicate[0] and "score 95.000" in duplicate[0]
    assert "Do not propose it again" in provider.prompts[-1]


def test_search_accepts_provider_adapter_and_sends_exact_residual(monkeypatch):
    class Provider:
        provider_id = "test-provider"

        def __init__(self):
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            return (
                '{"kind":"expression","hypothesis":"fix constant",'
                '"edits":[{"old":"return 0;","new":"return 1;"}]}',
                {"eval_count": 7})

    provider = Provider()
    monkeypatch.setattr(modelrepair.workspace, "target_asm",
                        lambda _ws, _name: "glabel f\njr ra\nnop")
    monkeypatch.setattr(modelrepair.workspace, "assert_uncontaminated",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        modelrepair.workspace, "score",
        lambda *_args, **_kwargs: _attempt(100.0, exact=True, diff=""))

    result = modelrepair.search(
        Path("repo"), "f", "int f(void) { return 0; }", Path("ws"),
        model="strong-or-local", endpoint="provider://test",
        provider=provider, base_attempt=_attempt(94.0), draws=1,
        max_depth=1, beam_width=1)

    assert result.exact
    assert len(provider.requests) == 1
    prompt = provider.requests[0].prompt
    assert "weighted_progress_score" in prompt
    assert "this is NOT percent bytes" in prompt
    # Prompts are compacted (prompt_compaction): pretty JSON is re-encoded without spaces, content unchanged.
    assert '"exact":false' in prompt.replace('": ', '":')


def test_search_enforces_global_model_call_budget(monkeypatch):
    monkeypatch.setattr(
        modelrepair.llm, "generate",
        lambda *_args, **_kwargs: ("not json", {"eval_count": 1}))
    monkeypatch.setattr(modelrepair.workspace, "target_asm",
                        lambda _ws, _name: "glabel f\njr ra\nnop")
    monkeypatch.setattr(modelrepair.workspace, "assert_uncontaminated",
                        lambda *_args, **_kwargs: None)

    result = modelrepair.search(
        Path("repo"), "f", "int f(void) { return 0; }", Path("ws"),
        model="local", endpoint="http://local", base_attempt=_attempt(),
        draws=4, max_depth=4, beam_width=3, max_calls=3,
        exhaust_budget=True)

    assert result.calls_attempted == 3
    assert result.generations == 3
    assert result.log[-1] == "model-call budget exhausted at 3"


@pytest.mark.parametrize('budget', [1, 2])
def test_truncated_reasoning_gets_budgeted_emission_not_compiled_as_final(monkeypatch, budget):
    requests, compiled = [], []
    proposal = '{"kind":"expression","hypothesis":"fix constant","edits":[{"old":"return 0;","new":"return 1;"}]}'
    class Provider:
        provider_id = 'test'
        def generate(self, request):
            requests.append(request)
            if len(requests) == 1:
                return proposal, {'eval_count': 10, 'done_reason': 'length', '_fell_back_to_thinking': True}
            assert request.think == 'low'
            assert request.response_schema == modelrepair.EDIT_SCHEMA
            assert 'OUTPUT COMPLETION' in request.prompt
            return proposal, {'eval_count': 5, 'done_reason': 'stop'}
    monkeypatch.setattr(modelrepair.workspace, 'target_asm', lambda *args: 'glabel f\njr ra\nnop')
    monkeypatch.setattr(modelrepair.workspace, 'assert_uncontaminated', lambda *args: None)
    def score(*args, **kwargs):
        compiled.append(args[3])
        return _attempt(100, exact=True)
    monkeypatch.setattr(modelrepair.workspace, 'score', score)
    result = modelrepair.search(Path('repo'), 'f', 'int f(void) { return 0; }', Path('ws'),
        model='test', endpoint='unused', provider=Provider(), base_attempt=_attempt(),
        draws=1, max_depth=1, max_calls=budget)
    assert result.incomplete_responses == 1
    assert result.invalid_proposals == 0
    assert result.calls_attempted == budget
    assert len(compiled) == budget - 1
    assert result.exact == (budget == 2)
