"""GPT-OSS may investigate through bounded tools, never through a shell."""

from __future__ import annotations

from pathlib import Path

import pytest

from solver import toolagent, workspace


def _attempt(score=90.0, *, exact=False, diff="-lw v0,0(a0)\n+lw v0,4(a0)"):
    return workspace.Attempt(True, score, exact, diff, "", "")


class Provider:
    provider_id = "scripted"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return next(self.responses), {"eval_count": 5}


def test_parse_action_supports_tools_and_bounded_patch():
    inspect = toolagent.parse_action(
        '{"action":"inspect_definition","query":"RacePlayer"}')
    patch = toolagent.parse_action(
        '{"action":"patch","kind":"layout","hypothesis":"wrong field",'
        '"edits":[{"old":"return 0;","new":"return 1;"}]}')

    assert inspect.query == "RacePlayer"
    assert patch.proposal.edits[0].new == "return 1;"
    alias = toolagent.parse_action(
        '{"action":"inspect_header","path":"include/x.h",'
        '"start":1,"end":20}')
    assert alias.name == "read_header"
    long_read = toolagent.parse_action(
        '{"action":"read_header","path":"include/x.h",'
        '"start":1,"end":10000}')
    assert long_read.end == 10000


def test_header_tools_never_read_source_tree(tmp_path):
    include = tmp_path / "include"
    source = tmp_path / "src"
    include.mkdir()
    source.mkdir()
    (include / "types.h").write_text("typedef struct Foo { int value; } Foo;\n")
    (source / "target.c").write_text("SECRET TARGET C\n")

    found = toolagent.inspect_definition(tmp_path, "Foo")
    denied = toolagent.read_header(tmp_path, "src/target.c", 1, 10)

    assert "include/types.h" in found
    assert "SECRET" not in found
    assert denied.startswith("DENIED")


def test_open_book_reads_siblings_but_redacts_target_definition(tmp_path):
    toolagent.clear_search_index()
    source = tmp_path / "src"
    source.mkdir()
    (source / "unit.c").write_text(
        "int sibling(void) { return 7; }\n\n"
        "int target_fn(int x) {\n"
        "    int direct_answer = x + 42;\n"
        "    return direct_answer;\n"
        "}\n")

    rendered = toolagent.read_path(
        tmp_path, tmp_path, "target_fn", "target", "src/unit.c", 1, 20)
    searched = toolagent.search_repo(
        tmp_path, tmp_path, "target_fn", "target", "direct_answer")

    assert "sibling" in rendered
    assert "DENIED: reference C definition of target_fn" in rendered
    assert "direct_answer" not in rendered
    assert "no project text matched" in searched


def test_open_book_search_reuses_raw_text_index_and_redacts_per_target(
        monkeypatch, tmp_path):
    toolagent.clear_search_index()
    source = tmp_path / "src"
    source.mkdir()
    path = source / "unit.c"
    path.write_text(
        "int first_target(void) { return 41; }\n"
        "int second_target(void) { return 42; }\n")
    original = Path.read_text
    reads = []

    def counted(self, *args, **kwargs):
        reads.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted)
    first = toolagent.search_repo(
        tmp_path, tmp_path, "first_target", "target", "return")
    second = toolagent.search_repo(
        tmp_path, tmp_path, "second_target", "target", "return")

    assert reads.count(path) == 1
    assert "return 42" in first and "return 41" not in first
    assert "return 41" in second and "return 42" not in second


def test_parse_open_book_actions():
    search = toolagent.parse_action(
        '{"action":"search_repo","root":"workbench","query":"Attempt"}')
    read = toolagent.parse_action(
        '{"action":"read_path","root":"target","path":"src/a.c",'
        '"start":1,"end":300}')
    rewrite = toolagent.parse_action(
        '{"action":"replace_source","hypothesis":"reshape function",'
        '"source":"int f(void) { return 1; }"}')

    assert search.root == "workbench"
    assert read.end == 300
    assert rewrite.source.endswith("}")
    both = toolagent.parse_action(
        '{"action":"search_repo","root":"target|workbench",'
        '"query":"symbol"}')
    broad_kind = toolagent.parse_action(
        '{"action":"patch","kind":"register-allocation",'
        '"hypothesis":"change a temporary lifetime",'
        '"edits":[{"old":"return 0;","new":"return value;"}]}')
    assert both.root == "both"
    assert broad_kind.proposal.kind == "other"


def test_tool_search_can_inspect_then_patch_exact(monkeypatch, tmp_path):
    include = tmp_path / "include"
    include.mkdir()
    (include / "types.h").write_text(
        "typedef struct Foo {\n    int value;\n} Foo;\n")
    provider = Provider([
        '{"action":"inspect_definition","query":"Foo"}',
        '{"action":"read_header","path":"include/types.h",'
        '"start":1,"end":3}',
        '{"action":"patch","kind":"expression",'
        '"hypothesis":"constant is wrong",'
        '"edits":[{"old":"return 0;","new":"return 1;"}]}',
    ])
    monkeypatch.setattr(toolagent.workspace, "target_asm",
                        lambda _ws, _name: "glabel f\njr ra\nnop")
    monkeypatch.setattr(toolagent.workspace, "assert_uncontaminated",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        toolagent.workspace, "score",
        lambda *_args, **_kwargs: _attempt(100.0, exact=True, diff=""))

    result = toolagent.search(
        tmp_path, "f", "int f(void) { return 0; }", tmp_path / "ws",
        model="local", endpoint="http://local", provider=provider,
        base_attempt=_attempt(), max_calls=3, max_compiles=1)

    assert result.exact
    assert result.tool_actions == 2
    assert result.compiles == 1
    assert all(request.prefill == '{"action":"'
               for request in provider.requests)
    assert "typedef struct Foo" in provider.requests[1].prompt
    assert "int value" in provider.requests[2].prompt


def test_open_book_search_can_replace_complete_source(monkeypatch, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "idioms.md").write_text("Use a full control-flow reshape.\n")
    provider = Provider([
        '{"action":"search_repo","root":"target","query":"reshape"}',
        '{"action":"replace_source","hypothesis":"use documented shape",'
        '"source":"int f(void) { return 1; }"}',
    ])
    monkeypatch.setattr(toolagent.workspace, "target_asm",
                        lambda _ws, _name: "glabel f\njr ra\nnop")
    monkeypatch.setattr(toolagent.workspace, "assert_uncontaminated",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        toolagent.workspace, "score",
        lambda *_args, **_kwargs: _attempt(100.0, exact=True, diff=""))

    result = toolagent.search(
        tmp_path, "f", "int f(void) { return 0; }", tmp_path / "ws",
        model="local", endpoint="http://local", provider=provider,
        base_attempt=_attempt(), max_calls=2, max_compiles=1,
        open_book=True, workbench_root=tmp_path,
        require_inspection_before_patch=False)

    assert result.exact
    assert result.tool_actions == 1
    assert result.compiles == 1
    assert "full control-flow reshape" in provider.requests[1].prompt


def test_tool_search_enforces_compile_budget(monkeypatch, tmp_path):
    provider = Provider([
        ('{"action":"patch","kind":"expression","hypothesis":"one",'
         '"edits":[{"old":"return 0;","new":"return 1;"}]}'),
        ('{"action":"patch","kind":"expression","hypothesis":"two",'
         '"edits":[{"old":"return 1;","new":"return 2;"}]}'),
    ])
    monkeypatch.setattr(toolagent.workspace, "target_asm",
                        lambda _ws, _name: "glabel f\njr ra\nnop")
    monkeypatch.setattr(toolagent.workspace, "assert_uncontaminated",
                        lambda *_args, **_kwargs: None)
    calls = []

    def score(*_args, **_kwargs):
        calls.append(1)
        return _attempt(91.0)

    monkeypatch.setattr(toolagent.workspace, "score", score)
    result = toolagent.search(
        tmp_path, "f", "int f(void) { return 0; }", tmp_path / "ws",
        model="local", endpoint="http://local", provider=provider,
        base_attempt=_attempt(), max_calls=2, max_compiles=1,
        require_inspection_before_patch=False)

    assert result.compiles == 1
    assert len(calls) == 1
    assert result.events[-1]["status"] == "compile-budget-exhausted"


def test_tool_search_requires_real_inspection_before_patch(monkeypatch, tmp_path):
    provider = Provider([
        ('{"action":"patch","kind":"expression","hypothesis":"guess",'
         '"edits":[{"old":"return 0;","new":"return 1;"}]}'),
        '{"action":"inspect_diff","view":"first"}',
    ])
    monkeypatch.setattr(toolagent.workspace, "target_asm",
                        lambda _ws, _name: "glabel f\njr ra\nnop")
    monkeypatch.setattr(toolagent.workspace, "assert_uncontaminated",
                        lambda *_args, **_kwargs: None)
    result = toolagent.search(
        tmp_path, "f", "int f(void) { return 0; }", tmp_path / "ws",
        model="local", endpoint="http://local", provider=provider,
        base_attempt=_attempt(), max_calls=2, max_compiles=1)

    assert result.compiles == 0
    assert result.events[0]["status"] == "inspection-required"
    assert result.tool_actions == 1


def test_tool_search_rejects_duplicate_inspection(monkeypatch, tmp_path):
    provider = Provider([
        '{"action":"inspect_diff","view":"first"}',
        '{"action":"inspect_diff","view":"first"}',
    ])
    monkeypatch.setattr(toolagent.workspace, "target_asm",
                        lambda _ws, _name: "glabel f\njr ra\nnop")
    monkeypatch.setattr(toolagent.workspace, "assert_uncontaminated",
                        lambda *_args, **_kwargs: None)
    result = toolagent.search(
        tmp_path, "f", "int f(void) { return 0; }", tmp_path / "ws",
        model="local", endpoint="http://local", provider=provider,
        base_attempt=_attempt(), max_calls=2, max_compiles=1)

    assert result.tool_actions == 1
    assert result.events[-1]["status"] == "duplicate-tool"


def test_curiosity_policy_rejects_unsupported_early_finish(
        monkeypatch, tmp_path):
    provider = Provider([
        '{"action":"finish","reason":"impossible"}',
        '{"action":"inspect_diff","view":"register"}',
        '{"action":"finish","reason":"now investigated"}',
    ])
    monkeypatch.setattr(toolagent.workspace, "target_asm",
                        lambda _ws, _name: "glabel f\njr ra\nnop")
    monkeypatch.setattr(toolagent.workspace, "assert_uncontaminated",
                        lambda *_args, **_kwargs: None)

    result = toolagent.search(
        tmp_path, "f", "int f(void) { return 0; }", tmp_path / "ws",
        model="local", endpoint="http://local", provider=provider,
        base_attempt=_attempt(), max_calls=3, max_compiles=1,
        open_book=True, require_inspection_before_patch=False,
        min_calls_before_finish=3, min_tool_actions_before_finish=1,
        principles=("[EXPERIMENTAL] test principle",))

    assert result.calls_attempted == 3
    assert result.events[0]["status"] == "curiosity-budget-unmet"
    assert result.events[-1]["status"] == "valid"
    assert "test principle" in provider.requests[0].prompt
