"""GPU-free tests for the local inference service.

Run from the repo root::

    python -m pytest tools/lora_serve/tests/test_lora_serve.py -v

Nothing here loads model weights or touches CUDA. The tokenizer-dependent tests
are skipped when the checkpoint directory is absent, so these pass on a machine
with no model at all. The live server test is skipped unless
``LOCAL_INFERENCE_URL`` is set.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tools.lora_serve import receipts as receipts_mod
from tools.lora_serve.chatfmt import ChatRenderError, render_chat, render_completion
from tools.lora_serve.client import (AdapterNotFound, InferenceClient, InferenceError,
                                     ServerNotRunning)

MODEL_PATH = os.environ.get("LOCAL_INFERENCE_MODEL",
                            "/home/grant/decomp/models/qwen2.5-coder-7b")
ADAPTER_PATH = os.environ.get("LOCAL_INFERENCE_ADAPTER",
                              "/home/grant/decomp/models/adapters/smoke")
LIVE_URL = os.environ.get("LOCAL_INFERENCE_URL")

_has_model = os.path.isdir(MODEL_PATH)
_has_adapter = os.path.isdir(ADAPTER_PATH)


@pytest.fixture(scope="module")
def tokenizer():
    if not _has_model:
        pytest.skip(f"checkpoint not present at {MODEL_PATH}")
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(MODEL_PATH)


# ---------------------------------------------------------------------------
# chat rendering / prefill
# ---------------------------------------------------------------------------
def test_prefill_is_spliced_into_the_assistant_turn(tokenizer):
    messages = [{"role": "user", "content": "write f"}]
    plain = render_chat(tokenizer, messages)
    prefilled = render_chat(tokenizer, messages, prefill="```c\n")
    assert prefilled.text == plain.text + "```c\n"
    assert prefilled.prefill == "```c\n"
    assert prefilled.text.rstrip().endswith("```c")
    # The prefill must land after the assistant header, not inside the user turn.
    assert "<|im_start|>assistant\n```c\n" in prefilled.text
    assert plain.text.endswith("<|im_start|>assistant\n")


def test_continue_final_message_equals_prefill(tokenizer):
    messages = [{"role": "user", "content": "write f"},
                {"role": "assistant", "content": "```c\n"}]
    via_continue = render_chat(tokenizer, messages, continue_final_message=True)
    via_prefill = render_chat(tokenizer, [{"role": "user", "content": "write f"}],
                              prefill="```c\n")
    assert via_continue.text == via_prefill.text
    assert via_continue.prefill == "```c\n"
    assert [m["role"] for m in via_continue.messages] == ["user"]


def test_naive_full_history_rendering_closes_the_turn(tokenizer):
    """The trap this module exists to avoid: the naive rendering ends the turn."""
    messages = [{"role": "user", "content": "write f"},
                {"role": "assistant", "content": "```c\n"}]
    naive = tokenizer.apply_chat_template(messages, tokenize=False,
                                          add_generation_prompt=False)
    assert naive.endswith("<|im_end|>\n")
    prefilled = render_chat(tokenizer, messages, continue_final_message=True)
    assert not prefilled.text.endswith("<|im_end|>\n")


def test_prefill_and_continue_final_message_conflict(tokenizer):
    with pytest.raises(ChatRenderError):
        render_chat(tokenizer, [{"role": "user", "content": "x"}],
                    prefill="```c\n", continue_final_message=True)


def test_continue_final_message_requires_assistant_last(tokenizer):
    with pytest.raises(ChatRenderError):
        render_chat(tokenizer, [{"role": "user", "content": "x"}],
                    continue_final_message=True)


def test_template_is_recorded_in_the_receipt_fields(tokenizer):
    rendered = render_chat(tokenizer, [{"role": "user", "content": "x"}], prefill="a")
    fields = rendered.receipt_fields()
    assert fields["rendered_prompt"] == rendered.text
    assert fields["templated"] is True
    assert fields["template_source"] == "tokenizer"
    # The checkpoint's template injects a default system turn; it must be visible.
    assert "You are Qwen" in fields["rendered_prompt"]


def test_render_rejects_bad_roles_and_content(tokenizer):
    with pytest.raises(ChatRenderError):
        render_chat(tokenizer, [{"role": "wizard", "content": "x"}])
    with pytest.raises(ChatRenderError):
        render_chat(tokenizer, [])
    with pytest.raises(ChatRenderError):
        render_chat(tokenizer, [{"role": "user", "content": [{"type": "image"}]}])


def test_render_completion_is_verbatim():
    rendered = render_completion("def f():\n")
    assert rendered.text == "def f():\n"
    assert rendered.templated is False
    assert rendered.prefill == ""


# ---------------------------------------------------------------------------
# default-adapter resolution: registering an adapter must not change what an
# unnamed request gets (the bug that silently routed every request through the
# known-degenerate smoke adapter)
# ---------------------------------------------------------------------------
from tools.lora_serve.server import resolve_default_adapter  # noqa: E402


def test_default_adapter_defaults_to_base():
    assert resolve_default_adapter(None, ["smoke"]) is None
    assert resolve_default_adapter("", ["smoke"]) is None
    assert resolve_default_adapter("none", ["smoke"]) is None
    assert resolve_default_adapter("base", ["smoke"]) is None
    assert resolve_default_adapter(None, []) is None


def test_default_adapter_auto_is_opt_in():
    assert resolve_default_adapter("auto", ["m1", "m2"]) == "m1"
    assert resolve_default_adapter("m2", ["m1", "m2"]) == "m2"


def test_default_adapter_rejects_unknown_name():
    with pytest.raises(SystemExit):
        resolve_default_adapter("nope", ["m1"])


def test_prepare_leaves_adapter_none_when_nothing_requests_one():
    """A request with no 'model' field must resolve to the base checkpoint."""
    class _Args:
        default_max_tokens = 256
        max_model_len = 4096
        max_n = 8
        allow_missing_chat_template = False

    class _Tokenizer:
        chat_template = "{{ messages[0]['content'] }}"

        def apply_chat_template(self, messages, tokenize=False,
                                add_generation_prompt=True, chat_template=None):
            return messages[-1]["content"]

    class _Backend:
        tokenizer = _Tokenizer()

        @staticmethod
        def encode(text):
            return [1, 2, 3]

    class _Service:
        args = _Args()
        backend = _Backend()

        @staticmethod
        def resolve_model(requested):
            return "base-model", resolve_default_adapter(requested, ["smoke"])

    from tools.lora_serve.server import prepare
    prepared = prepare(_Service(), {"messages": [{"role": "user", "content": "hi"}]},
                       "/v1/chat/completions")
    assert prepared.adapter_name is None
    assert prepared.model_id == "base-model"
    prepared_named = prepare(_Service(),
                             {"model": "smoke",
                              "messages": [{"role": "user", "content": "hi"}]},
                             "/v1/chat/completions")
    assert prepared_named.adapter_name == "smoke"


# ---------------------------------------------------------------------------
# receipts
# ---------------------------------------------------------------------------
def test_adapter_identity_hashes_content(tmp_path):
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text(json.dumps({"r": 16, "lora_alpha": 32}))
    (adapter / "adapter_model.safetensors").write_bytes(b"weights-v1")
    first = receipts_mod.adapter_identity(adapter)
    assert first["r"] == 16 and first["lora_alpha"] == 32
    assert first["combined_sha256"] and first["files"]
    (adapter / "adapter_model.safetensors").write_bytes(b"weights-v2")
    second = receipts_mod.adapter_identity(adapter)
    assert second["combined_sha256"] != first["combined_sha256"]


def test_real_adapter_identity_is_stable():
    if not _has_adapter:
        pytest.skip(f"adapter not present at {ADAPTER_PATH}")
    first = receipts_mod.adapter_identity(ADAPTER_PATH)
    second = receipts_mod.adapter_identity(ADAPTER_PATH)
    assert first["combined_sha256"] == second["combined_sha256"]
    assert first["config"].get("peft_type") == "LORA"
    assert any(name.endswith(".safetensors") for name in first["weights"])


def test_receipt_contains_every_required_field():
    timings = receipts_mod.GenerationTimings(queue_ms=1.0, tokenize_ms=2.0,
                                             prefill_ms=3.0, decode_ms=4.0, total_ms=10.0)
    receipt = receipts_mod.build_receipt(
        request_id="cmpl-test", created=time.time(), endpoint="/v1/chat/completions",
        model={"path": "/models/base", "config_sha256": "abc"},
        adapter={"combined_sha256": "def", "path": "/models/adapter"},
        adapter_requested="smoke",
        rendered={"rendered_prompt": "PROMPT", "rendered_history": "PROMPT",
                  "prefill": "", "templated": True, "template_source": "tokenizer",
                  "messages": [], "render_notes": []},
        prompt_tokens=7, prompt_token_ids_sha256="x", prompt_token_ids=[1, 2, 3],
        completion_tokens=5, finish_reason="stop", stop_reason={"type": "eos_token"},
        text="ANSWER", continuation="ANSWER",
        sampling={"temperature": 0.2, "top_p": 0.8, "top_k": 20, "seed": 1},
        timings=timings, server={"backend": "transformers"}, batch={"size": 1})
    for key in ["request_id", "model", "adapter", "adapter_hash", "rendered_prompt",
                "rendered_prompt_tokens" if False else "prompt_tokens",
                "completion_tokens", "finish_reason", "raw_response_text",
                "sampling", "timings_ms", "wall_ms", "prompt_token_ids"]:
        assert key in receipt, key
    assert receipt["schema"] == receipts_mod.RECEIPT_SCHEMA
    assert json.loads(json.dumps(receipt))["request_id"] == "cmpl-test"


# ---------------------------------------------------------------------------
# sampler
# ---------------------------------------------------------------------------
torch = pytest.importorskip("torch")
from tools.lora_serve.sampler import PerRowSampler, RowSampling  # noqa: E402


def _scores(batch=1, vocab=8, seed=0):
    generator = torch.Generator().manual_seed(seed)
    return torch.randn(batch, vocab, generator=generator) * 3.0


def test_sampler_is_deterministic_for_a_seed():
    rows = [RowSampling(temperature=1.0, top_p=0.9, top_k=0, repetition_penalty=None,
                        seed=1234)]
    draws = []
    for _ in range(3):
        sampler = PerRowSampler(rows, device="cpu")
        torch.manual_seed(torch.initial_seed())  # global RNG must not matter
        sampler(torch.zeros(1, 1, dtype=torch.long), _scores(seed=7))
        draws.append(sampler.chosen)
    assert draws[0] == draws[1] == draws[2]


def test_sampler_differs_across_seeds():
    def draw(seed):
        sampler = PerRowSampler([RowSampling(temperature=1.0, top_p=1.0, top_k=0,
                                             repetition_penalty=None, seed=seed)],
                                device="cpu")
        sampler(torch.zeros(1, 1, dtype=torch.long), _scores(seed=3))
        return sampler.chosen[0]
    assert len({draw(s) for s in range(12)}) > 1


def test_temperature_zero_is_greedy():
    scores = _scores(vocab=16, seed=11)
    sampler = PerRowSampler([RowSampling(temperature=0.0, top_p=1.0, top_k=0,
                                         repetition_penalty=None, seed=None)],
                            device="cpu")
    sampler(torch.zeros(1, 1, dtype=torch.long), scores)
    assert sampler.chosen[0] == int(torch.argmax(scores[0]))


def test_top_k_one_is_greedy():
    scores = _scores(vocab=16, seed=12)
    sampler = PerRowSampler([RowSampling(temperature=1.0, top_p=1.0, top_k=1,
                                         repetition_penalty=None, seed=99)],
                            device="cpu")
    sampler(torch.zeros(1, 1, dtype=torch.long), scores)
    assert sampler.chosen[0] == int(torch.argmax(scores[0]))


def test_sampler_forces_the_drawn_token():
    """The returned logits must put all mass on the drawn token."""
    scores = _scores(vocab=16, seed=13)
    sampler = PerRowSampler([RowSampling(temperature=1.0, top_p=1.0, top_k=0,
                                         repetition_penalty=None, seed=5)],
                            device="cpu")
    forced = sampler(torch.zeros(1, 1, dtype=torch.long), scores)
    assert int(torch.argmax(forced[0])) == sampler.chosen[0]


def test_repetition_penalty_pushes_down_seen_tokens():
    vocab = 8
    scores = torch.zeros(1, vocab)
    scores[0, 3] = 5.0
    scores[0, 5] = 3.0            # second best: wins once 3 is penalised to 2.5
    seen = torch.tensor([[3]], dtype=torch.long)
    plain = PerRowSampler([RowSampling(temperature=0.0, repetition_penalty=None)],
                          device="cpu")
    plain(seen, scores.clone())
    penalised = PerRowSampler([RowSampling(temperature=0.0, repetition_penalty=2.0)],
                              device="cpu")
    penalised(seen, scores.clone())
    assert plain.chosen[0] == 3
    assert penalised.chosen[0] == 5


def test_batch_rows_are_sampled_independently():
    rows = [RowSampling(temperature=0.0, repetition_penalty=None),
            RowSampling(temperature=0.0, repetition_penalty=None)]
    scores = torch.zeros(2, 4)
    scores[0, 0] = 10.0
    scores[1, 2] = 10.0
    sampler = PerRowSampler(rows, device="cpu")
    sampler(torch.zeros(2, 1, dtype=torch.long), scores)
    assert sampler.chosen == [0, 2]


def test_batch_size_mismatch_is_loud():
    sampler = PerRowSampler([RowSampling()], device="cpu")
    with pytest.raises(RuntimeError):
        sampler(torch.zeros(2, 1, dtype=torch.long), torch.zeros(2, 4))


# ---------------------------------------------------------------------------
# client against a stub server (no GPU, no model)
# ---------------------------------------------------------------------------
class _StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    receipt_enabled = True

    def log_message(self, *args):  # noqa: A003
        return

    def _json(self, payload, status=200):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self._json({"status": "ok", "served_name": "stub"})
        elif self.path == "/v1/models":
            self._json({"object": "list", "data": [{"id": "stub", "object": "model"}]})
        elif self.path.startswith("/receipts/"):
            self._json({"request_id": self.path.rsplit("/", 1)[-1]})
        else:
            self._json({"error": {"message": "nope"}}, status=404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if body.get("model") == "missing":
            self._json({"error": {"message": "unknown model 'missing'"}}, status=404)
            return
        self.server.last_request = body  # type: ignore[attr-defined]
        receipt = {
            "schema": receipts_mod.RECEIPT_SCHEMA, "request_id": "cmpl-stub",
            "rendered_prompt": "RENDERED", "prompt_tokens": 3, "completion_tokens": 2,
            "adapter_hash": "deadbeef", "wall_ms": 12.5, "continuation_text": "ont",
            "raw_response_text": "pfxont",
        }
        payload = {
            "id": "cmpl-stub", "object": "chat.completion", "created": 1,
            "model": body.get("model") or "stub",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "pfxont"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        if body.get("receipt") and self.receipt_enabled:
            payload["receipt"] = receipt
        self._json(payload)


@pytest.fixture()
def stub_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server
    finally:
        server.shutdown()
        server.server_close()


def test_client_sends_prefill_and_sampling(stub_server):
    url, server = stub_server
    client = InferenceClient(url)
    result = client.chat([{"role": "user", "content": "hi"}], prefill="pfx",
                         temperature=0.2, top_p=0.8, top_k=20, seed=4, max_tokens=64,
                         repetition_penalty=1.1)
    sent = server.last_request  # type: ignore[attr-defined]
    assert sent["prefill"] == "pfx"
    assert sent["temperature"] == 0.2 and sent["top_k"] == 20 and sent["seed"] == 4
    assert sent["receipt"] is True
    assert result.text == "pfxont"
    assert result.continuation == "ont"
    assert result.prompt_tokens == 3 and result.completion_tokens == 2
    assert result.receipt["wall_ms"] == 12.5
    assert result.adapter_hash == "deadbeef"


def test_client_omits_unset_sampling_so_server_defaults_apply(stub_server):
    url, server = stub_server
    InferenceClient(url).chat([{"role": "user", "content": "hi"}])
    sent = server.last_request  # type: ignore[attr-defined]
    for key in ("temperature", "top_p", "top_k", "seed", "max_tokens"):
        assert key not in sent, key


def test_client_fails_loudly_when_receipt_is_expected_but_absent(stub_server):
    url, server = stub_server
    _StubHandler.receipt_enabled = False
    try:
        with pytest.raises(InferenceError):
            InferenceClient(url).chat([{"role": "user", "content": "hi"}])
        # ... unless the caller says receipts are not required
        result = InferenceClient(url, require_receipt=False).chat(
            [{"role": "user", "content": "hi"}])
        assert result.receipt is None
    finally:
        _StubHandler.receipt_enabled = True


def test_client_maps_missing_model_to_adapter_not_found(stub_server):
    url, _ = stub_server
    with pytest.raises(AdapterNotFound):
        InferenceClient(url).chat([{"role": "user", "content": "hi"}], model="missing")


def test_client_reports_dead_server():
    client = InferenceClient("http://127.0.0.1:9", timeout=2.0)
    with pytest.raises(ServerNotRunning):
        client.chat([{"role": "user", "content": "hi"}])


def test_client_health_and_models(stub_server):
    url, _ = stub_server
    client = InferenceClient(url)
    assert client.health()["status"] == "ok"
    assert client.models()[0]["id"] == "stub"
    assert client.is_ready() is True


# ---------------------------------------------------------------------------
# live server (opt-in)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not LIVE_URL, reason="set LOCAL_INFERENCE_URL to test a live server")
def test_live_server_returns_a_complete_receipt():
    client = InferenceClient(LIVE_URL, timeout=600)
    info = client.wait_until_ready(timeout=120)
    result = client.chat([{"role": "user", "content": "Reply with the word OK."}],
                         prefill="", temperature=0.0, max_tokens=16,
                         receipt_token_ids=True)
    receipt = result.require_receipt()
    assert receipt["prompt_tokens"] > 0
    assert receipt["rendered_prompt"]
    assert receipt["model"]["path"] == info["model_path"]
    assert receipt["adapter_hash"] == info["adapters"].get(receipt["adapter_requested"])
    assert receipt["wall_ms"] > 0
    assert receipt["prompt_token_ids"]
    assert client.receipt_for(result.request_id)["request_id"] == result.request_id


# --- continuous batching (server --continuous) -------------------------------------------------------------------
from tools.lora_serve.backends import SequenceResult, SequenceSpec  # noqa: E402
from tools.lora_serve.server import ContinuousScheduler, Job  # noqa: E402


class _StepEngine:
    """Each sequence needs max_tokens steps; step() finishes whoever is done."""

    def __init__(self, fail_at_step=None):
        self.left, self.steps, self.fail_at_step = {}, 0, fail_at_step

    def add(self, spec):
        rid = f"{spec.request_id}#{spec.index}"
        self.left[rid] = spec.max_tokens
        return rid

    def step(self):
        self.steps += 1
        time.sleep(0.002)
        if self.fail_at_step == self.steps:
            raise RuntimeError("engine died")
        done = []
        for rid in list(self.left):
            self.left[rid] -= 1
            if self.left[rid] <= 0:
                del self.left[rid]
                done.append((rid, rid))
        return done

    def has_unfinished(self):
        return bool(self.left)

    def abort(self, ids):
        for rid in ids:
            self.left.pop(rid, None)

    def result_of(self, spec, output, started, running):
        return SequenceResult(request_id=spec.request_id, index=spec.index, continuation=output, token_ids=[],
                              finish_reason="stop", stop_reason={}, prefill_ms=0, decode_ms=0, batch_size=running,
                              adapter_name=None)


def _job(rid, tokens):
    return Job(request_id=rid, specs=[SequenceSpec(request_id=rid, index=0, prompt="p", prompt_ids=[1],
                                                   max_tokens=tokens)])


def test_continuous_scheduler_finishes_short_requests_while_long_ones_run():
    engine = _StepEngine()
    sched = ContinuousScheduler(engine)
    sched.start()
    long_job, short_job = _job("long", 400), _job("short", 3)
    sched.submit(long_job)
    sched.submit(short_job)
    assert short_job.wait(5) and short_job.error is None
    assert not long_job.event.is_set()            # the short one did not wait for the long one
    assert long_job.wait(5) and long_job.results[0].continuation == "long#0"
    sched.stop()


def test_continuous_scheduler_fails_inflight_jobs_when_the_engine_fails():
    sched = ContinuousScheduler(_StepEngine(fail_at_step=2))
    sched.start()
    job = _job("a", 50)
    sched.submit(job)
    assert job.wait(5) and "engine died" in job.error
    sched.stop()


def test_continuous_scheduler_fails_requests_the_engine_does_not_know():
    """The first live run stalled: finished outputs came back under another id, so nothing was delivered and
    step() blocked on an idle engine. A request the engine has no record of must fail, not hang."""
    class Forgetful(_StepEngine):
        def add(self, spec):
            return f"{spec.request_id}#{spec.index}"     # never registered with the engine
    sched = ContinuousScheduler(Forgetful())
    sched.start()
    job = _job("a", 5)
    sched.submit(job)
    assert job.wait(5) and "no record" in job.error
    sched.stop()


def test_admin_operations_wait_for_inflight_requests_and_sleep_refuses_work():
    from tools.lora_serve.server import ApiError
    engine = _StepEngine()
    sched = ContinuousScheduler(engine)
    sched.start()
    job = _job("long", 200)
    sched.submit(job)
    seen = {}
    sched.admin(lambda: seen.update(inflight_done=job.event.is_set()))
    assert seen["inflight_done"]                  # ran only after the request in flight had finished
    sched.asleep = True
    with pytest.raises(ApiError, match="asleep"):
        sched.submit(_job("x", 1))
    sched.stop()
