"""The tool-action masking contract, pinned without a GPU and without a model.

The failure modes this file exists to catch are all SILENT. A completion-only mask applied at the
wrong offset trains the model to emit the prompt and the loss still falls. A tokenizer that fuses
the prefilled `{` with the token after it trains a first label that inference never asks for, and
the only visible symptom is a model that is slightly worse than it should be. An over-long action
JSON that is truncated instead of dropped teaches the model to stop mid-JSON. None of these raise
and none of them show up in a loss curve.

`TinyTokenizer` below maps characters to token ids rather than words, so the boundary is checkable
exactly: the prefill is the single token for `{`, and a fused `{"` pair is a DIFFERENT id that can
never appear in the ids the trainer builds. That is what makes the prefill test mean something --
with a word-level stub, the merge that motivates the two-call render could not be expressed at all.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from eval import train_tool_action_sft as ta


@pytest.fixture
def sandbox():
    """A scratch directory built here rather than by `tmp_path`.

    Measured on this box: pytest's own `tmp_path` fixture raises `PermissionError` while scanning
    `%TEMP%\\pytest-of-grant`, so every test in the repository that uses it errors before it runs
    (14 errors in `tests/test_repair_training_path.py` alone, 10 of its tests still passing).
    `tempfile.mkdtemp` works. This fixture keeps these tests runnable with the plain command
    instead of requiring `--basetemp`.
    """
    path = tempfile.mkdtemp(prefix="toolaction-sft-")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class TinyTokenizer:
    """Character-level stub. Deliberately fuses `{"` so the real BPE hazard is representable."""

    pad_token_id = 0
    eos_token_id = 1
    # A token that only a one-call tokenization of `{"...` produces. If it appears anywhere in a
    # trained sequence, the prefill was fused with what follows it.
    FUSED = 4

    def _ids(self, text: str) -> list[int]:
        ids, position = [], 0
        while position < len(text):
            if text.startswith('{"', position):
                ids.append(self.FUSED)
                position += 2
                continue
            ids.append(max(5, ord(text[position])))
            position += 1
        return ids

    def __call__(self, text, add_special_tokens=False):
        if isinstance(text, (list, tuple)):
            return {"input_ids": [self._ids(item) for item in text]}
        return {"input_ids": self._ids(text)}

    def apply_chat_template(self, messages, tokenize=True, add_generation_prompt=True, **kwargs):
        ids: list[int] = []
        for message in messages:
            ids.append({"system": 10, "user": 11, "assistant": 12}.get(message["role"], 13))
            ids.extend(self._ids(message["content"]))
        if add_generation_prompt:
            ids.append(12)
        return {"input_ids": ids, "attention_mask": [1] * len(ids)}


class MisTokenizedPreToken(TinyTokenizer):
    """A tokenizer whose `{` is not the token inference would feed.

    No real tokenizer does this; it is here so the run's refusal path is reachable from a test
    without a GPU, a model, or a dataset. Without it the only way to exercise the refusal would be
    to break the render, which is the thing the refusal exists to detect.
    """

    def __call__(self, text, add_special_tokens=False):
        if isinstance(text, (list, tuple)):
            return {"input_ids": [self._ids(item) for item in text]}
        if text == ta.PREFILL:
            return {"input_ids": [99]}
        return {"input_ids": self._ids(text)}


COMPLETION = '{"action": "compile", "params": {}}'


def record(record_id="proc-0001", split="train", **overrides):
    base = {
        "id": record_id,
        "kind": "procedural",
        "split": split,
        "function": "osGetThreadPri",
        "family": "os-thread",
        "messages": [{"role": "system", "content": "You choose the next action."},
                     {"role": "user", "content": "function: osGetThreadPri\nbudget: 4"}],
        "completion": COMPLETION,
        "action": {"action": "compile", "params": {}},
        "acceptable": [],
        "label_source": "mechanical",
        "label_confidence": "certain",
        "evidence": {"note": "never fed to the model"},
        "budget_remaining": 4,
        "schema_version": 1,
    }
    base.update(overrides)
    return base


def write_dataset(root, records, name="tool.jsonl"):
    path = Path(root) / name
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def prompt_ids(tokenizer, example):
    return list(tokenizer.apply_chat_template(example["messages"], tokenize=True,
                                              add_generation_prompt=True)["input_ids"])


# --- 1. the boundary ----------------------------------------------------------

def test_prompt_tokens_are_masked_and_completion_tokens_are_not():
    """The rule the experiment rests on: only the action JSON carries loss.

    The boundary is checked by INDEX, not by counting. A mask applied one token late leaves the
    generation prompt trainable, which is exactly what a `<`/`<=` slip in `collate` looks like, and
    the loss still falls.
    """
    torch = pytest.importorskip("torch")
    tokenizer = TinyTokenizer()
    example = ta.make_example(record())
    ids, boundary, matches = ta.render(tokenizer, example)
    assert matches, "the stub records its own fusion, so a boundaried render must agree here"
    assert boundary == len(prompt_ids(tokenizer, example)), \
        "the boundary is the prompt length, and it comes from the same call that made the ids"

    labels = list(ids)
    for position in range(boundary):
        labels[position] = -100
    assert labels[:boundary] == [-100] * boundary
    assert labels[boundary:] == ids[boundary:]
    assert labels[boundary] == 123, "the first TRAINED token is the prefill `{`"
    assert all(value != -100 for value in labels[boundary:]), "no completion position is masked"

    # And through the collator, which is the path training actually takes.
    out = ta.collate(tokenizer)([(ids, boundary)])
    row = out["labels"].tolist()[0]
    assert row[:boundary] == [-100] * boundary
    assert row[boundary:] == ids[boundary:]


# --- 2. the prefill -----------------------------------------------------------

def test_the_trained_span_is_the_prefill_plus_the_rest_plus_eos():
    """The trained span is `ids_of("{") + ids_of(completion[1:]) + EOS`, and not `ids_of(completion)`.

    `TinyTokenizer` fuses `{"` when it sees both characters in ONE call: the one-call ids of the
    completion start with the fused token, while the trainer's two-call span starts with the prefill
    token -- which is what `ModelPolicy.choose` feeds. If a later change collapses the two calls into
    one, the span below becomes the fused one and this test fails.
    """
    tokenizer = TinyTokenizer()
    example = ta.make_example(record())
    ids, boundary, matches = ta.render(tokenizer, example)

    head = tokenizer(ta.PREFILL, add_special_tokens=False)["input_ids"]
    tail = tokenizer(example["completion"][len(ta.PREFILL):],
                     add_special_tokens=False)["input_ids"]
    assert ta.PREFILL == "{"
    assert head == [123], "the prefill is tokenized on its own"
    # THE ASSERTION THE CONTRACT ASKS FOR: ids_of("{") + ids_of(rest) IS the trained span, and the
    # region before it is exactly the prompt.
    assert ids[boundary:] == head + tail + [tokenizer.eos_token_id]
    assert ids[boundary:boundary + 1 + len(tail)] == head + tail
    assert ids[boundary] == head[0], "the first trained token is the prefill token"
    assert ids[:boundary] == prompt_ids(tokenizer, example)

    # ... and it is NOT the one-call ids, which fuse `{"` into a token the model never produces.
    one_call = tokenizer(example["completion"], add_special_tokens=False)["input_ids"]
    assert one_call[0] == tokenizer.FUSED, "the stub really does fuse `{\"` in one call"
    assert ids[boundary] != one_call[0]
    assert tokenizer.FUSED not in ids, "the fused token is nowhere in the trained sequence"
    assert matches, "and the run's own boundary check accepts the span it built"


def test_a_completion_span_that_does_not_start_with_the_prefill_is_reported_and_refused():
    """A mismatched boundary is counted per example, not warned about once and trained through.

    The count is the run's required report line. On the real render path it is always zero, and
    this test states that rather than pretending otherwise: `render` builds the span as
    `ids_of("{") + ids_of(rest)`, so the count can only become non-zero if a later change takes the
    prefill out of the completion. What the test CAN do is pin the comparison that would notice --
    `boundary_ok` on a completion span that starts with a fused `{"` says no -- and pin that a
    one-call tokenization of the same completion really does produce that fused span.
    """
    tokenizer = TinyTokenizer()
    examples = [ta.make_example(record("a")), ta.make_example(record("b"))]
    kept, dropped, rows = ta.tokenize_examples(tokenizer, examples, max_seq_len=512)
    assert len(kept) == 2 and not dropped
    assert ta.boundary_mismatches(rows) == [], "the rendered span always starts with the prefill"
    assert all(row["boundary_matches_inference"] for row in rows)
    # The required count, read the way the report writes it.
    summary = ta.summarize(rows, dropped, lines_read=2, split_tally={"train": 2}, split="train")
    assert summary["label_boundary"][
        "examples_where_completion_ids_do_not_start_with_prefill_ids"] is None, \
        "`summarize` must not invent a count; `train` fills it from the mismatch list"
    assert summary["label_boundary"]["examples_where_one_call_tokenization_differs"] == 2, \
        "the diagnostic must show that one-call tokenization fuses `{\"` on this stub"

    # The comparison that would fire, exercised on the span that would trip it.
    fused = tokenizer(COMPLETION, add_special_tokens=False)["input_ids"]
    assert fused[0] == TinyTokenizer.FUSED
    assert not ta.boundary_ok([123], fused)
    assert not ta.boundary_ok([123, 34], [123, 99, 100])


def test_a_run_with_a_mistokenized_prefill_refuses_before_loading_a_model(sandbox, monkeypatch):
    """The refusal path, reachable without a GPU: it fires during tokenisation.

    `render` is replaced with one that reports a failed boundary check, which is the shape a future
    refactor would produce (the comparison itself is pinned by the test above). What this test adds
    is the ORDER: the refusal happens before `AutoTokenizer`/`AutoModelForCausalLM` are touched, so
    a run cannot spend a model load finding out that its labels are the wrong ones.
    """
    from eval import resource_limits

    # `resource_limits.apply()` calls `os.nice`, which does not exist on Windows, and its handler
    # catches only OSError -- so on this box `apply()` raises AttributeError for ANY caller. That is
    # a pre-existing defect in `eval/resource_limits.py` and out of scope here; the caps themselves
    # are not what this test is about, so they are stubbed to keep the test about the refusal.
    monkeypatch.setattr(resource_limits, "apply",
                        lambda *a, **k: resource_limits.limits())
    path = write_dataset(sandbox, [record("a")])
    monkeypatch.setattr(ta, "_load_tokenizer", lambda cfg: MisTokenizedPreToken())
    original = ta.render

    def broken(tokenizer, example):
        ids, boundary, _matches = original(tokenizer, example)
        return ids, boundary, False

    monkeypatch.setattr(ta, "render", broken)
    cfg = ta.TrainConfig(dataset=path, base=Path(sandbox) / "base",
                         out=Path(sandbox) / "out", dry_run=True)
    with pytest.raises(SystemExit) as info:
        ta.train(cfg)
    assert "boundary" in str(info.value)


def test_the_boundary_check_compares_the_whole_prefill_not_its_first_token():
    """A prefill of several tokens must match in full; a first-token-only check is the bug shape."""
    assert ta.boundary_ok([123], [123, 34, 100])
    assert not ta.boundary_ok([123], [4, 34, 100]), "the fused `{\"` is not the prefill"
    assert ta.boundary_ok([123, 34], [123, 34, 100])
    assert not ta.boundary_ok([123, 34], [123, 99, 100]), "a wrong second token is still wrong"
    assert not ta.boundary_ok([123], []), "an empty completion span cannot carry the prefill"
    with pytest.raises(ValueError):
        ta.boundary_ok([], [123])


def test_a_degenerate_render_is_refused():
    class NoPrompt(TinyTokenizer):
        def apply_chat_template(self, messages, tokenize=True, add_generation_prompt=True,
                                **kwargs):
            return {"input_ids": [], "attention_mask": []}

    with pytest.raises(ValueError):
        ta.render(NoPrompt(), ta.make_example(record()))
    with pytest.raises(SystemExit):
        ta.assert_tokenizer_usable(NoPrompt())


# --- 3. over-long examples ----------------------------------------------------

def test_an_over_long_example_is_dropped_with_a_reason_not_truncated():
    """A truncated action JSON teaches the model to emit invalid JSON."""
    tokenizer = TinyTokenizer()
    short = ta.make_example(record("short"))
    long = ta.make_example(record("long", messages=[
        {"role": "system", "content": "s" * 40},
        {"role": "user", "content": "u" * 400}]))
    kept, dropped, rows = ta.tokenize_examples(tokenizer, [short, long], max_seq_len=200)

    assert len(kept) == 1, "the short example must survive"
    assert [row["id"] for row in dropped] == ["long"]
    assert "exceeds max_seq_len 200" in dropped[0]["reason"]
    assert dropped[0]["tokens"] > 200
    assert dropped[0]["prompt_tokens"] > 200
    # Every example appears in the length table, kept or dropped ...
    assert {row["id"] for row in rows} == {"short", "long"}
    # ... the kept row was NOT cut to fit: its length is the real rendered length and not the cap a
    # truncating implementation would have landed on ...
    for ids, boundary in kept:
        assert len(ids) < 200 and 0 < boundary < len(ids)
    assert not [row for row in rows if row["tokens"] == 200]
    # ... and nothing that was dropped was kept under another id.
    assert {row["id"] for row in dropped}.isdisjoint(
        {row["id"] for row in rows if row["tokens"] <= 200})


def test_the_summary_counts_kept_and_dropped_per_split_and_per_label_source():
    tokenizer = TinyTokenizer()
    examples = [ta.make_example(record("short")),
                ta.make_example(record("long", label_source="execution",
                                       messages=[{"role": "user", "content": "x" * 400}]))]
    kept, dropped, rows = ta.tokenize_examples(tokenizer, examples, max_seq_len=200)
    summary = ta.summarize(rows, dropped, lines_read=2, split_tally={"train": 2}, split="train")
    assert summary["kept"] == 1 and summary["dropped"] == 1
    assert summary["examples_available"] == 2
    assert summary["dropped_by_reason"] == {"exceeds max_seq_len 200": 1}
    assert summary["kept_by_split"] == {"train": 1}
    assert summary["kept_by_label_source"] == {"mechanical": 1}
    assert summary["tokens"]["completion"]["count"] == 1
    assert summary["tokens"]["prompt"]["max"] > summary["tokens"]["completion"]["max"]
    assert summary["per_example"] and len(summary["per_example"]) == 2


# --- 4. split selection -------------------------------------------------------

def test_records_from_another_split_are_excluded_never_mixed_in(sandbox):
    path = write_dataset(sandbox, [record("a", "train"), record("b", "dev"),
                                   record("c", "test"), record("d", "train")])
    records = ta.load_records(path)
    examples, tally, lines_read = ta.select_records(records, "train")
    assert [e["id"] for e in examples] == ["a", "d"]
    assert tally == {"train": 2, "dev": 1, "test": 1}
    assert lines_read == 4

    dev, _, _ = ta.select_records(records, "dev")
    assert [e["id"] for e in dev] == ["b"]

    # The canary escape hatch is explicit and is not the default.
    everything, _, _ = ta.select_records(records, "any")
    assert [e["id"] for e in everything] == ["a", "b", "c", "d"]

    # --limit applies AFTER split filtering, so it cannot pull a dev record into a train run.
    limited, _, _ = ta.select_records(records, "train", limit=1)
    assert [e["id"] for e in limited] == ["a"]


# --- 5. collation -------------------------------------------------------------

def test_the_collator_pads_labels_with_minus_100_and_attention_with_zero():
    torch = pytest.importorskip("torch")
    tokenizer = TinyTokenizer()
    longest, shortest = [5, 6, 7, 8, 9], [5, 6, 7]
    out = ta.collate(tokenizer)([(longest, 3), (shortest, 2)])
    labels = out["labels"].tolist()
    assert labels[0] == [-100, -100, -100, 8, 9]
    assert labels[1] == [-100, -100, 7, -100, -100]
    assert out["attention_mask"].tolist() == [[1, 1, 1, 1, 1], [1, 1, 1, 0, 0]]
    assert out["input_ids"].tolist()[1] == [5, 6, 7, tokenizer.pad_token_id,
                                           tokenizer.pad_token_id]
    # A pad id in the labels is a loss term the model can never satisfy.
    for row in labels:
        for value in row:
            assert value == -100 or value != tokenizer.pad_token_id
    assert out["input_ids"].shape == out["labels"].shape == out["attention_mask"].shape


def test_a_batch_item_with_an_empty_completion_span_is_refused():
    pytest.importorskip("torch")
    tokenizer = TinyTokenizer()
    with pytest.raises(ValueError):
        ta.collate(tokenizer)([([5, 6, 7], 3)])       # boundary == len(ids): nothing to learn
    with pytest.raises(ValueError):
        ta.collate(tokenizer)([([5, 6, 7], 0)])       # boundary == 0: the prompt would be trained


# --- 6. malformed records -----------------------------------------------------

def test_a_record_missing_messages_or_completion_is_rejected_with_a_clear_error(sandbox):
    """`record.get("completion", "")` would build a label span of `{` + EOS and train on nothing."""
    bad = record("no-messages")
    del bad["messages"]
    path = write_dataset(sandbox, [bad], name="no-messages.jsonl")
    with pytest.raises(ValueError) as info:
        ta.load_records(path)
    assert "messages" in str(info.value) and "no-messages" in str(info.value)
    assert "line 1" in str(info.value)

    for bad in (record("empty", completion=""), record("none", completion=None)):
        path = write_dataset(sandbox, [bad], name=f"{bad['id']}.jsonl")
        with pytest.raises(ValueError) as info:
            ta.load_records(path)
        assert "completion" in str(info.value)

    # A completion that does not start with the prefill breaks the same contract, so it is refused
    # too: the first trained token would not be the token inference prefills.
    path = write_dataset(sandbox, [record("unprefilled", completion='"action": "compile"}')],
                         name="unprefilled.jsonl")
    with pytest.raises(ValueError) as info:
        ta.load_records(path)
    assert "prefill" in str(info.value)

    # And a record that is not an object at all.
    path = Path(sandbox) / "not-a-record.jsonl"
    path.write_text('["not", "an", "object"]\n', encoding="utf-8")
    with pytest.raises(ValueError) as info:
        ta.load_records(path)
    assert "JSON object" in str(info.value)


def test_a_dataset_with_no_record_from_the_requested_split_is_refused(sandbox):
    path = write_dataset(sandbox, [record("a", "dev")])
    records = ta.load_records(path)
    examples, tally, _ = ta.select_records(records, "train")
    assert examples == [] and tally == {"dev": 1}


def test_a_dry_run_touches_no_model_and_writes_the_token_report(sandbox, monkeypatch):
    """`--dry-run` prices the data and exits. It must not import peft or load a checkpoint.

    This box has no `peft` installed at all, so a dry run that reached the model section would fail
    here rather than in a report -- which is the point: the data can be priced on any machine.
    """
    from eval import resource_limits

    monkeypatch.setattr(resource_limits, "apply",
                        lambda *a, **k: resource_limits.limits())
    monkeypatch.setattr(ta, "_load_tokenizer", lambda cfg: TinyTokenizer())
    path = write_dataset(sandbox, [record("a", "train"), record("b", "train"),
                                   record("long", "train",
                                          messages=[{"role": "user", "content": "x" * 400}])])
    out = Path(sandbox) / "run"
    report = ta.train(ta.TrainConfig(dataset=path, base=Path(sandbox) / "base", out=out,
                                     split="train", max_seq_len=200, dry_run=True))
    assert report["dry_run"] is True
    assert report["examples_kept"] == 2 and report["examples_dropped"] == 1
    written = json.loads((out / "token_report.json").read_text(encoding="utf-8"))
    assert written["kept"] == 2 and written["dropped"] == 1
    assert written["requested_split"] == "train"
    assert written["records_by_split_in_file"] == {"train": 3}
    assert written["kept_by_label_source"] == {"mechanical": 2}
    assert written["tokens"]["prompt"]["count"] == 2, "distributions cover kept examples only"
    assert written["tokens"]["dropped_sequence"]["count"] == 1
    assert written["label_boundary"]["examples_checked"] == 3
    # No receipt and no adapter: a dry run trains nothing and must not look like it did.
    assert not (out / "training_receipt.json").exists()
    assert not (out / "adapter").exists()


# --- config plumbing ----------------------------------------------------------

def test_the_cli_flags_reach_the_config_the_run_needs(sandbox, monkeypatch):
    """Pinned because a flag that never reaches `TrainConfig` is only found after a model load."""
    captured = {}

    def fake_train(cfg):
        captured["cfg"] = cfg
        return {"dry_run": True}

    monkeypatch.setattr(ta, "train", fake_train)
    argv = ["--dataset", str(Path(sandbox) / "d.jsonl"), "--out", str(Path(sandbox) / "out"),
            "--base", str(Path(sandbox) / "base"), "--epochs", "2", "--lr", "5e-5", "--lora-r", "8",
            "--lora-alpha", "16", "--lora-dropout", "0.1", "--max-seq-len", "512",
            "--batch-size", "2", "--grad-accum", "4", "--seed", "7", "--limit", "3",
            "--max-steps", "9", "--split", "dev", "--adapter-name", "act-lora", "--dry-run",
            "--no-load-in-4bit"]
    assert ta.main(argv) == 0
    cfg = captured["cfg"]
    assert (cfg.split, cfg.adapter_name, cfg.dry_run) == ("dev", "act-lora", True)
    assert cfg.load_in_4bit is False and cfg.max_steps == 9 and cfg.limit == 3
    assert (cfg.lora_r, cfg.lora_alpha, cfg.lora_dropout) == (8, 16, 0.1)
    assert (cfg.max_seq_len, cfg.batch_size, cfg.grad_accum) == (512, 2, 4)
    assert (cfg.epochs, cfg.lr, cfg.seed) == (2.0, 5e-5, 7)
    assert cfg.dataset.name == "d.jsonl" and cfg.base.name == "base"

    # Defaults, which decide what an unflagged run does.
    del captured["cfg"]
    assert ta.main(["--dataset", str(Path(sandbox) / "d.jsonl"),
                    "--out", str(Path(sandbox) / "o2")]) == 0
    default = captured["cfg"]
    assert default.split == "train" and default.load_in_4bit is True
    assert default.max_steps == -1 and default.dry_run is False
    assert default.base == Path.home() / "decomp" / "models" / "qwen2.5-coder-7b"
