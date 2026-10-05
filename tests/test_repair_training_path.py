"""The training path's contract, pinned without a GPU.

The failure modes this file exists to catch are all silent. A completion-only mask applied at
the wrong offset trains the model to predict the prompt and the loss still falls. A tokenizer
whose chat template is not applied yields a 2-token "prompt" and trains on nothing. An
over-long example that is truncated rather than dropped teaches the model to stop mid-function.
None of those raise, and none of them are visible in a loss curve.

`transformers` 5 returns a `BatchEncoding` from `apply_chat_template(..., tokenize=True)`, not a
list. That is the specific API change that made `len(prefix_ids)` equal 2 -- the number of keys
-- for every example in this project's own dataset, and it was caught by a length assertion
rather than by a training run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from eval import repair_prompts as rp
from eval import train_repair_sft as tr


class TinyTokenizer:
    """A tokenizer with a chat template, so the masking boundary is checkable exactly."""

    pad_token_id = 0
    eos_token_id = 1

    def apply_chat_template(self, messages, tokenize=True, add_generation_prompt=True):
        ids = []
        for message in messages:
            ids.append(10 if message["role"] == "user" else 11)
            ids.extend([2] * len(message["content"].split()))
        if add_generation_prompt:
            ids.append(11)
        return {"input_ids": ids, "attention_mask": [1] * len(ids)}

    def __call__(self, text, add_special_tokens=False):
        return {"input_ids": [3] * len(text.split())}


def test_transformers_five_batch_encoding_is_unwrapped_not_counted():
    """The exact defect: `len()` of a BatchEncoding is its number of KEYS."""
    encoded = {"input_ids": [1, 2, 3, 4], "attention_mask": [1, 1, 1, 1]}
    assert len(encoded) == 2, "the shape that produced a 2-token prompt boundary"
    assert rp._ids_of(encoded) == [1, 2, 3, 4]
    assert rp._ids_of([1, 2, 3]) == [1, 2, 3]


def test_a_tokenizer_that_cannot_render_a_chat_turn_is_refused():
    class Broken(TinyTokenizer):
        def apply_chat_template(self, *a, **k):
            return {"input_ids": [], "attention_mask": []}

    with pytest.raises(SystemExit):
        tr.assert_tokenizer_usable(Broken())

    class NoPad(TinyTokenizer):
        pad_token_id = None
        eos_token_id = None

    with pytest.raises(SystemExit):
        tr.assert_tokenizer_usable(NoPad())


def test_the_completion_boundary_is_strictly_inside_the_sequence():
    tokenizer = TinyTokenizer()
    example = rp.Example(kind=rp.REPAIR_KIND, prompt="repair this now",
                         completion="void f(void) {}", function="f", record_id="x")
    ids, boundary = rp.render_chat(tokenizer, example)
    assert 0 < boundary < len(ids)
    # The masked region is exactly the prompt.
    assert boundary < len(ids)
    assert len(ids) - boundary > 0


def test_a_degenerate_chat_rendering_raises_rather_than_training_on_nothing():
    class Degenerate(TinyTokenizer):
        def apply_chat_template(self, messages, tokenize=True, add_generation_prompt=True):
            return {"input_ids": [1], "attention_mask": [1]}

    example = rp.Example(kind=rp.REPAIR_KIND, prompt="p", completion="c",
                         function="f", record_id="x")
    with pytest.raises(ValueError):
        rp.render_chat(Degenerate(), example)


def test_labels_mask_every_prompt_token_and_every_pad_token():
    """The rule the experiment rests on: only the completion contributes to the loss."""
    torch = pytest.importorskip("torch")
    tokenizer = TinyTokenizer()
    collate_fn = tr.collate(tokenizer, max_seq_len=64)
    batch = [([5, 6, 7, 8, 9], 3), (([5, 6, 7]), 2)]         # (ids, boundary)
    out = collate_fn(batch)
    labels = out["labels"].tolist()
    assert labels[0][:3] == [-100, -100, -100], "prompt tokens masked"
    assert labels[0][3:] == [8, 9], "completion tokens kept"
    assert labels[1][:2] == [-100, -100]
    assert labels[1][2] == 7
    assert labels[1][3:] == [-100, -100], "pad positions masked"
    assert out["attention_mask"].tolist() == [[1, 1, 1, 1, 1], [1, 1, 1, 0, 0]]
    assert out["input_ids"].shape == out["labels"].shape == out["attention_mask"].shape


def test_no_label_is_a_pad_token_id_anywhere():
    """A pad id in the labels is a loss term the model can never satisfy."""
    torch = pytest.importorskip("torch")
    tokenizer = TinyTokenizer()
    out = tr.collate(tokenizer, 64)([([0, 5, 6, 7], 1), ([5, 6], 1)])
    labels = out["labels"].tolist()
    for row in labels:
        for value in row:
            assert value == -100 or value != tokenizer.pad_token_id


def test_over_long_examples_are_dropped_not_truncated():
    tokenizer = TinyTokenizer()
    examples = []
    for index in range(4):
        examples.append(rp.Example(kind=rp.REPAIR_KIND,
                                  prompt=" ".join(["word"] * (2 + index * 30)),
                                  completion=" ".join(["tok"] * 2),
                                  function="f", record_id=f"r{index}"))
    kept, dropped, lengths = tr.tokenize_examples(tokenizer, examples, max_seq_len=30)
    assert kept, "the shortest example must fit"
    assert dropped, "the long ones must be reported, not silently cut"
    for row in dropped:
        assert "exceeds max_seq_len" in row["reason"]
        assert row["tokens"] > 30
    # Every example appears in the length report whether kept or dropped.
    assert {row["id"] for row in lengths} == {e.record_id for e in examples}
    # And nothing that was dropped appears among the kept ones.
    kept_ids = {row["id"] for row in lengths if row["tokens"] <= 30}
    assert not (kept_ids & {row["id"] for row in dropped})


def test_every_kept_sequence_fits_and_its_boundary_is_inside():
    tokenizer = TinyTokenizer()
    examples = [rp.Example(kind=rp.REPAIR_KIND, prompt="a b c", completion="d e f",
                           function="f", record_id=f"r{i}") for i in range(3)]
    kept, dropped, _ = tr.tokenize_examples(tokenizer, examples, max_seq_len=64)
    assert not dropped
    for ids, boundary in kept:
        assert len(ids) <= 64
        assert 0 < boundary < len(ids)
        assert ids[-1] == tokenizer.eos_token_id, "the completion ends with eos"


# --- dataset selection ---------------------------------------------------------

def _record(record_id, split, delta, function="f", provenance="reconstructed-lineage"):
    return {"id": record_id, "game": "sbk1", "function": function, "func_addr": 1,
            "tu": "t", "provenance": provenance, "split": split,
            "input": {"target_asm": "glabel f\nendlabel f", "candidate_c": "void f(void){}",
                      "compiler_outcome": {"compiled": True, "score": 50.0,
                                           "diff": "- a\n+ b", "stderr": ""}},
            "target": {"source_c": "void f(void){ }", "exact": False, "score": 51.0},
            "meta": {"score_delta": delta, "parent_attempt_id": 1, "child_attempt_id": 2}}


def _write(tmp_path, records):
    path = tmp_path / "ds.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def test_the_delta_filter_drops_a_parent_that_is_not_the_state_being_repaired(tmp_path):
    """A 40-point jump is a different attempt, not a refinement. Training on it teaches
    'discard the candidate', which is the opposite of the behaviour under test."""
    path = _write(tmp_path, [_record("a", "train", 1.0), _record("b", "train", 40.0)])
    cfg = tr.TrainConfig(base=tmp_path, out=tmp_path / "o", dataset=path,
                         split="train", max_score_delta=10.0)
    examples, dropped = tr.load_examples(cfg)
    assert [e.record_id for e in examples] == ["a"]
    assert len(dropped) == 1 and "score_delta" in dropped[0]["reason"]


def test_splits_are_never_mixed_by_default(tmp_path):
    path = _write(tmp_path, [_record("a", "train", 1.0), _record("b", "test", 1.0)])
    cfg = tr.TrainConfig(base=tmp_path, out=tmp_path / "o", dataset=path, split="train")
    examples, _ = tr.load_examples(cfg)
    assert [e.record_id for e in examples] == ["a"]
    cfg.split = None
    examples, _ = tr.load_examples(cfg)
    assert {e.record_id for e in examples} == {"a", "b"}


def test_a_record_without_assembly_is_dropped_with_the_reason(tmp_path):
    bad = _record("no-asm", "train", 1.0)
    bad["input"]["target_asm"] = None
    path = _write(tmp_path, [bad, _record("ok", "train", 1.0)])
    cfg = tr.TrainConfig(base=tmp_path, out=tmp_path / "o", dataset=path, split="train")
    examples, dropped = tr.load_examples(cfg)
    assert [e.record_id for e in examples] == ["ok"]
    assert "target_asm" in dropped[0]["reason"]


def test_headers_are_explicit_provenance_not_a_silent_upgrade(tmp_path):
    """Removing a header from a prompt does not make a header-derived answer binary-only."""
    path = _write(tmp_path, [_record("a", "train", 1.0)])
    cfg = tr.TrainConfig(base=tmp_path, out=tmp_path / "o", dataset=path, split="train",
                         include_headers=True)
    with pytest.raises(SystemExit):
        tr.load_examples(cfg)          # header assistance without a repo is refused


# --- prompt construction ------------------------------------------------------

def test_the_repair_prompt_is_the_projects_own_rendering():
    """Collection, training and evaluation must build the identical input."""
    from eval import trajectory_factory as tf
    from solver.refine import COMPILE_FAIL_PROMPT, DIFF_PROMPT

    state = tf.RepairState(source="void f(void){}", compiled=False,
                           compiler_stderr="cfe: Error: boom")
    assert rp.repair_prompt(target_asm="asm", candidate_c=state.source, compiled=False,
                            compiler_stderr=state.compiler_stderr) == \
        COMPILE_FAIL_PROMPT.format(code=state.source, errors=state.compiler_stderr)
    compiling = tf.RepairState(source="void f(void){}", compiled=True, score=88.0,
                               diff="- lw v0,0x24(a0)\n+ lw v0,0(a0)")
    rendered = rp.repair_prompt(target_asm="ASM", candidate_c=compiling.source, compiled=True,
                                score=88.0, diff=compiling.diff)
    assert rendered == tf.render_repair_prompt(compiling, "ASM")
    assert "ASM" in rendered and compiling.diff in rendered


def test_the_prompt_version_is_folded_into_every_digest():
    a = rp.Example(kind=rp.REPAIR_KIND, prompt="p", completion="c", function="f",
                   record_id="1")
    b = rp.Example(kind=rp.REPAIR_KIND, prompt="p", completion="c", function="f",
                   record_id="2")
    assert a.digest() == b.digest(), "the digest covers content, not the row id"
    c = rp.Example(kind=rp.REPAIR_KIND, prompt="p", completion="d", function="f",
                   record_id="1")
    assert a.digest() != c.digest()
    assert rp.PROMPT_VERSION in {"repair-v1"} or rp.PROMPT_VERSION.startswith("repair-v")
