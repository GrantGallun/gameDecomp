"""Tests for the hosted-agent provider and its Windows-side shim.

Two things are worth testing here and they are not the HTTP plumbing. First,
the isolation guards are an argument list, so they are asserted literally: a
silently dropped `--ignore-user-config` would let the user's hooks into a
measured trial and nothing else would notice. Second, the tool-use audit is the
contamination check for the whole experiment -- the reference decomp is the
answer key -- so it is tested on a stream that DOES contain tool calls, not
only on a clean one.
"""

import json
import subprocess

import pytest

from solver import codexprovider
from solver.modelrepair import GenerationRequest
from tools import codex_shim


def request(**overrides):
    fields = dict(prompt="repair this", model="gpt-5.3-codex-spark",
                  endpoint="", timeout=120, think="", num_thread=0,
                  temperature=0.2, num_predict=6000, seed=7,
                  cache_dir=None, cache_namespace="test", prefill="",
                  response_schema=None)
    fields.update(overrides)
    return GenerationRequest(**fields)


# ---- shim: the guards are the product ---------------------------------

def test_command_carries_every_isolation_guard(tmp_path):
    command = codex_shim.build_command(
        executable="codex.cmd", model="gpt-5.3-codex-spark",
        workdir=tmp_path / "empty", schema_path=tmp_path / "schema.json",
        output_path=tmp_path / "last.txt")
    assert command[:3] == ["codex.cmd", "exec", "-"]      # prompt from stdin
    for guard in ("--ephemeral", "--ignore-user-config", "--skip-git-repo-check",
                  "--json"):
        assert guard in command, guard
    assert command[command.index("-s") + 1] == "read-only"
    assert command[command.index("-C") + 1] == str(tmp_path / "empty")
    assert command[command.index("--output-schema") + 1] == str(tmp_path / "schema.json")


def test_schema_flag_is_omitted_when_no_schema_is_requested(tmp_path):
    command = codex_shim.build_command(
        executable="codex", model="m", workdir=tmp_path, schema_path=None,
        output_path=tmp_path / "last.txt")
    assert "--output-schema" not in command


def test_audit_fires_on_the_event_shape_this_cli_really_emits():
    """Captured verbatim from codex-cli 0.144.3 running one shell command.

    The tool call is nested under `item`, so an audit that only matched outer
    event types reported a clean run while the agent shelled out.
    """
    stream = "\n".join([
        "not json at all",
        json.dumps({"type": "thread.started", "thread_id": "01a0928e"}),
        json.dumps({"type": "item.completed", "item": {
            "id": "item_0", "type": "agent_message", "text": "Running it now."}}),
        json.dumps({"type": "item.started", "item": {
            "id": "item_1", "type": "command_execution",
            "command": "powershell.exe -Command 'echo hi'", "exit_code": None}}),
        json.dumps({"type": "item.completed", "item": {
            "id": "item_1", "type": "command_execution",
            "command": "powershell.exe -Command 'echo hi'",
            "aggregated_output": "hi", "exit_code": 0}}),
        json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 23003, "cached_input_tokens": 20096,
            "output_tokens": 268, "reasoning_output_tokens": 162}}),
    ])
    audit = codex_shim.audit_events(stream)
    assert audit["tool_call_count"] == 1        # started + completed is ONE call
    assert audit["tool_calls"][0]["type"] == "command_execution"
    assert audit["tool_calls"][0]["exit_code"] == 0
    assert audit["tokens"]["input"] == 23003
    assert audit["tokens"]["output"] == 268
    assert audit["tokens"]["reasoning"] == 162
    assert audit["tokens"]["total"] == 23271    # summed when not reported
    assert audit["events_parsed"] == 5          # the noise line is not counted


def test_audit_also_fires_on_the_older_flat_event_shape():
    stream = "\n".join([
        json.dumps({"msg": {"type": "exec_command_begin",
                            "command": ["grep", "-r", "answer", "/decomp"],
                            "cwd": "/tmp"}}),
        json.dumps({"msg": {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": 900, "output_tokens": 120,
                                  "total_tokens": 1020}}}}),
    ])
    audit = codex_shim.audit_events(stream)
    assert audit["tool_call_count"] == 1
    assert audit["tool_calls"][0]["command"] == ["grep", "-r", "answer", "/decomp"]
    assert audit["tokens"]["total"] == 1020


def test_audit_stays_quiet_on_a_clean_generation():
    stream = "\n".join([
        json.dumps({"type": "item.completed", "item": {
            "id": "item_0", "type": "agent_message", "text": "done"}}),
        json.dumps({"type": "turn.completed",
                    "usage": {"input_tokens": 10, "output_tokens": 2}}),
    ])
    audit = codex_shim.audit_events(stream)
    assert audit["tool_call_count"] == 0 and audit["tool_calls"] == []


def test_generate_reads_the_last_message_file_and_reports_the_audit():
    seen = {}

    def runner(command, **kwargs):
        seen["command"] = command
        seen["input"] = kwargs.get("input")
        output = command[command.index("-o") + 1]
        with open(output, "w", encoding="utf-8") as handle:
            handle.write('{"kind": "layout"}')
        return subprocess.CompletedProcess(
            command, 0,
            stdout=json.dumps({"type": "item.completed", "item": {
                "id": "item_1", "type": "command_execution", "command": "ls"}}),
            stderr="")

    result = codex_shim.generate(
        {"prompt": "fix", "model": "m", "timeout": 30,
         "response_schema": {"type": "object"}},
        executable="codex.cmd", runner=runner)

    assert result["text"] == '{"kind": "layout"}'
    assert seen["input"] == "fix"               # prompt goes over stdin, not argv
    assert result["meta"]["tool_call_count"] == 1
    assert result["meta"]["returncode"] == 0


def test_the_prompt_is_sent_as_utf8_whatever_the_windows_codepage_is():
    """Regression: text mode defaulted to cp1252 and the CLI rejected the lot.

    A single character outside the locale codepage made Codex refuse the prompt
    before reading it, which the kernel recorded as `empty-response` -- the same
    thing it records when a model has nothing to say. A measured "the model
    failed on hard functions" would have been an encoding default.
    """
    seen = {}

    def runner(command, **kwargs):
        seen.update(kwargs)
        output = command[command.index("-o") + 1]
        with open(output, "w", encoding="utf-8") as handle:
            handle.write("ok")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    codex_shim.generate({"prompt": "diff ─ non-cp1252 ✓", "timeout": 30},
                        executable="codex", runner=runner)
    assert seen["encoding"] == "utf-8"
    assert seen["input"] == "diff ─ non-cp1252 ✓"


def test_a_rejected_schema_is_retried_unconstrained_and_recorded():
    """The kernel's proposal schema is outside the CLI's strict subset.

    Fires on the real failure: the first invocation exits nonzero with no
    answer, which the kernel would otherwise record as the model declining.
    """
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        output = command[command.index("-o") + 1]
        if "--output-schema" in command:
            return subprocess.CompletedProcess(command, 1, stdout="",
                                               stderr="unsupported keyword oneOf")
        with open(output, "w", encoding="utf-8") as handle:
            handle.write('{"kind": "layout", "edits": []}')
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = codex_shim.generate(
        {"prompt": "fix", "timeout": 30,
         "response_schema": {"type": "object", "oneOf": [{"required": ["a"]}]}},
        executable="codex", runner=runner)

    assert len(calls) == 2                       # constrained, then not
    assert "--output-schema" in calls[0] and "--output-schema" not in calls[1]
    assert result["text"] == '{"kind": "layout", "edits": []}'
    assert result["meta"]["schema_fallback"] is True
    assert result["meta"]["schema_requested"] is True
    assert "schema rejected" in result["meta"]["stderr_tail"]


def test_a_working_schema_is_not_retried():
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        output = command[command.index("-o") + 1]
        with open(output, "w", encoding="utf-8") as handle:
            handle.write('{"kind": "layout"}')
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = codex_shim.generate(
        {"prompt": "fix", "timeout": 30, "response_schema": {"type": "object"}},
        executable="codex", runner=runner)
    assert len(calls) == 1 and result["meta"]["schema_fallback"] is False


def test_a_spent_quota_raises_instead_of_returning_nothing():
    """Fires on the real incident, not a hypothetical.

    The CLI prints the limit message on STDOUT and exits 1, so a shim reading
    only stderr sees a blank failure. Four functions of a six-function cohort
    were recorded as `incomplete_responses` -- indistinguishable from a model
    declining on hard targets -- before this check existed.
    """
    message = ("ERROR: You've hit your usage limit for GPT-5.3-Codex-Spark. "
               "Switch to another model now, or try again at 10:13 PM.")

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout=message, stderr="")

    with pytest.raises(codex_shim.QuotaExhausted, match="usage limit"):
        codex_shim.generate({"prompt": "fix", "timeout": 30},
                            executable="codex", runner=runner)


def test_the_provider_turns_a_429_into_a_stop_not_a_null():
    import urllib.error
    import io
    failure = urllib.error.HTTPError(
        "u", 429, "too many requests", {},
        io.BytesIO(json.dumps({"error": "usage limit reached",
                               "quota_exhausted": True}).encode()))
    provider = StubProvider([failure, payload("never reached")],
                            transport_attempts=2)
    with pytest.raises(codexprovider.QuotaExhausted, match="usage limit"):
        provider.generate(request())
    assert len(provider.posts) == 1              # not retried into the limit


def test_generate_refuses_an_empty_prompt():
    with pytest.raises(ValueError):
        codex_shim.generate({"prompt": "   "}, executable="codex",
                            runner=lambda *a, **k: None)


# ---- provider: protocol differences must stay visible ------------------

class StubProvider(codexprovider.CodexProvider):
    def __init__(self, payloads, **kwargs):
        super().__init__(endpoint="http://stub:11436", model="stub-model",
                         **kwargs)
        self.payloads = list(payloads)
        self.posts = []

    def _post(self, body, timeout):
        self.posts.append(body)
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return payload


def payload(text="ok", **meta):
    base = {"eval_count": 11, "tool_call_count": 0}
    base.update(meta)
    return {"text": text, "meta": base}


def test_generate_returns_text_and_marks_itself_nondeterministic():
    provider = StubProvider([payload("candidate")])
    text, meta = provider.generate(request(response_schema={"type": "object"}))
    assert text == "candidate"
    assert meta["deterministic"] is False       # no seed or temperature exists
    assert meta["honoured_temperature"] is None
    assert meta["requested_temperature"] == 0.2
    assert meta["eval_count"] == 11             # the kernel bills tokens on this
    assert provider.posts[0]["response_schema"] == {"type": "object"}
    assert meta["_transport_events"][0]["status"] == "response"


def test_prefill_is_appended_to_the_prompt_and_labelled():
    provider = StubProvider([payload()])
    _text, meta = provider.generate(request(prefill='```json\n{"kind":'))
    assert meta["prefill_mode"] == "appended-to-user-prompt"
    sent = provider.posts[0]["prompt"]
    assert sent.startswith("repair this")
    assert sent.endswith('```json\n{"kind":')


def test_second_call_with_the_same_seed_is_served_from_cache(tmp_path):
    provider = StubProvider([payload("first")])
    first, meta = provider.generate(request(cache_dir=tmp_path))
    assert meta["_cache_hit"] is False
    second, meta2 = provider.generate(request(cache_dir=tmp_path))
    assert (second, meta2["_cache_hit"]) == (first, True)
    assert len(provider.posts) == 1             # no second model call was made


def test_a_different_seed_is_an_independent_draw(tmp_path):
    provider = StubProvider([payload("first"), payload("second")])
    first, _ = provider.generate(request(cache_dir=tmp_path, seed=1))
    second, _ = provider.generate(request(cache_dir=tmp_path, seed=2))
    assert (first, second) == ("first", "second")
    assert len(provider.posts) == 2


def test_caching_without_a_seed_is_refused(tmp_path):
    provider = StubProvider([payload()])
    with pytest.raises(ValueError, match="explicit seed"):
        provider.generate(request(cache_dir=tmp_path, seed=None))


def test_a_tampered_cache_entry_is_not_silently_used(tmp_path):
    provider = StubProvider([payload("first")])
    _text, meta = provider.generate(request(cache_dir=tmp_path))
    path = tmp_path / meta["_cache_key"][:2] / f"{meta['_cache_key']}.json"
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["key_material"]["model"] = "some-other-model"
    path.write_text(json.dumps(entry), encoding="utf-8")
    with pytest.raises(ValueError, match="cache key mismatch"):
        provider.generate(request(cache_dir=tmp_path))


def test_a_transient_shim_failure_is_retried_then_recorded():
    import urllib.error
    failure = urllib.error.HTTPError("u", 503, "unavailable", {}, None)
    provider = StubProvider([failure, payload("after retry")],
                            transport_attempts=2)
    codexprovider.time.sleep = lambda _s: None
    text, meta = provider.generate(request())
    assert text == "after retry"
    assert [event["status"] for event in meta["_transport_events"]] == \
        ["error", "response"]


def test_a_bad_request_is_not_retried():
    import urllib.error
    failure = urllib.error.HTTPError("u", 400, "bad", {}, None)
    provider = StubProvider([failure], transport_attempts=2)
    with pytest.raises(urllib.error.HTTPError):
        provider.generate(request())


def test_the_factory_satisfies_the_kernel_provider_protocol(monkeypatch):
    monkeypatch.setenv("CODEX_SHIM_ENDPOINT", "http://stub:11436")
    built = codexprovider.provider()
    assert built.provider_id == "codex-cli" and callable(built.generate)
    assert built.endpoint == "http://stub:11436"
