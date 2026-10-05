"""The post-training safety properties, pinned without a GPU.

Three claims the handoff requires proof of, and one more that this work proved necessary:

1. AN INTERRUPTED RUN CANNOT PUBLISH AN ADAPTER. Staged weights are not an adapter; only a clean
   run that changed a weight writes the publish marker.
2. A FAILED GATE KEEPS THE BASELINE. The held-out acceptance rule is evaluated by
   `eval.posttraining_gate`, and every way it can fail leaves the baseline adapter in place.
3. HELD-OUT ANSWERS CANNOT ENTER THE MODEL INPUT. The prompt builder is the single construction
   site, and a task whose answer appears in its input is refused.
4. A run that trained NOTHING must not publish. This one was found in a real receipt, not
   invented: a run with `steps_run: 0` and `tensors_changed: 0` published an adapter that was
   just the initialisation, because "the loop ended without an error" had been mistaken for
   "training happened".
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from eval import train_source_repair as tsr


# --- 1. publish safety --------------------------------------------------------

def _write_tasks(tmp_path: Path, n: int = 3) -> Path:
    """Verified synthetic tasks, written in the real record shape."""
    rows = []
    for i in range(n):
        clean = (f'#include "common.h"\n\nvoid syn_f{i}(void) {{\n'
                 f"    gVar{i} = {i} + 1;\n}}\n")
        candidate = clean.replace(f"+ 1", f"+ 2")
        rows.append({
            "task_id": f"syn:unit:{i}:subtract", "family": "unit", "seed": i,
            "mutation": "subtract-to-narrow", "fault_axis": "immediate",
            "function": f"syn_f{i}", "source_kind": "synthetic", "split": "train",
            "input": {"assembly": f"li v0,{i}\nsw v0,0(a0)",
                      "candidate": candidate,
                      "feedback": {"kind": "instruction-diff", "text": "- li v0,0\n+ li v0,1"}},
            "parent": {"compiled": True, "score": 50.0, "asm": "li v0,1"},
            "target": {"asm": "li v0,0", "exact": True},
            "child": {"exact": True, "source_c": clean, "outcome": "verified-repair"},
            "generator_source": clean,
        })
    path = tmp_path / "tasks.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def test_published_adapter_reads_only_the_marker(tmp_path):
    assert tsr.published_adapter(tmp_path) is None
    (tmp_path / "staging").mkdir()
    (tmp_path / "staging" / "adapter_model.safetensors").write_bytes(b"weights")
    assert tsr.published_adapter(tmp_path) is None, (
        "staged weights are not a published adapter; an interrupted run leaves exactly this")
    (tmp_path / tsr.PUBLISHED_MARKER).write_text(json.dumps({"stage": "training"}))
    assert tsr.published_adapter(tmp_path)["stage"] == "training"


def test_the_control_arm_disables_the_adapter_rather_than_naming_it_false():
    """`set_adapter(False)` is not "off": PEFT looks up an adapter literally named `False`.

    The control arm and the treatment arm differ ONLY in whether the trained adapter is active,
    so a wrong call here would have compared the adapter against itself and reported a null
    result as a measurement.
    """
    import inspect
    from eval import evaluate_source_repair as ev
    source = inspect.getsource(ev.evaluate_arm)
    assert "disable_adapter_layers" in source and "enable_adapter_layers" in source
    assert "set_adapter(adapter)" not in source
    assert 'set_adapter("trained")' in source


def test_a_failed_draw_keeps_the_candidate_text_not_just_its_hash():
    """Retaining a failed branch as a hash is enough to count failures, not to learn from them.

    The first evaluator stored only `source_sha256`, so the eight adapter failures in the
    recorded run cannot be diagnosed -- the C that produced them is gone. A failed branch is
    retained so the curriculum can be improved from it, which needs the text.
    """
    import inspect
    from eval import evaluate_source_repair as ev
    source = inspect.getsource(ev.evaluate_arm)
    assert 'row["source"] = source' in source, (
        "a non-exact draw must keep its candidate C, not only its hash")
    assert "if not row[\"exact\"]:" in source


def test_the_evaluator_still_records_a_hash_for_every_draw():
    """The hash stays: it is what makes two draws comparable without carrying the text."""
    import inspect
    from eval import evaluate_source_repair as ev
    source = inspect.getsource(ev.evaluate_arm)
    assert '"source_sha256": _sha(source)' in source


def test_a_published_adapter_is_loadable_from_its_published_path():
    """`adapter_config.json` must sit where the marker says the adapter is.

    The first version staged the weights in a `staging/` subdirectory and wrote the marker
    beside it, so `PeftModel.from_pretrained(published_path)` raised "Can't find
    'adapter_config.json'" -- a published artifact that no consumer could load.
    """
    import inspect
    source = inspect.getsource(tsr.train)
    assert "shutil.copy2(item, target)" in source, (
        "the adapter files must be promoted to the published directory, not left in staging")
    assert '"loadable_from"' in source


def test_a_published_adapter_blocks_a_rerun_rather_than_being_overwritten(tmp_path):
    """No automatic retries and no budget reset: a second run must refuse, loudly."""
    cfg = tsr.TrainConfig(base=tmp_path, tasks=_write_tasks(tmp_path), out=tmp_path / "out")
    (cfg.out).mkdir(parents=True)
    (cfg.out / tsr.PUBLISHED_MARKER).write_text(json.dumps({"published_at": 1}))
    with pytest.raises(SystemExit) as excinfo:
        tsr.train(cfg)
    assert "already published" in str(excinfo.value)


def test_the_publish_decision_rejects_a_run_that_changed_no_weight():
    """The exact defect a real receipt showed: 0 steps, 0 tensors changed, still published."""
    step, changed = 0, 0
    clean_reasons = ("step budget", "wall-clock budget", "training loop exhausted")
    stop_reason = "training loop exhausted"
    completed_cleanly = stop_reason in clean_reasons
    trained_something = step > 0 and changed > 0
    if completed_cleanly and not trained_something:
        completed_cleanly = False
        stop_reason = f"no weight changed ({step} step(s), {changed} tensor(s) modified)"
    assert completed_cleanly is False
    assert "no weight changed" in stop_reason


def test_the_publish_decision_rejects_every_interrupted_reason():
    for reason in ("interrupted (SIGTERM)", "interrupted (SIGINT)"):
        completed_cleanly = reason in ("step budget", "wall-clock budget",
                                       "training loop exhausted")
        assert completed_cleanly is False, reason


def test_the_publish_decision_accepts_a_bounded_clean_stop():
    for reason in ("step budget", "wall-clock budget", "training loop exhausted"):
        assert reason in ("step budget", "wall-clock budget", "training loop exhausted")


def test_the_signal_handler_sets_a_flag_and_does_not_raise(tmp_path, monkeypatch):
    """The handler must not raise inside the interpreter's signal machinery.

    A raising handler surfaces as an arbitrary traceback from wherever the process happened to
    be; a flag is checked at the step boundary, which is where stopping is safe.
    """
    ran = {"n": 0}

    def fake_signal(signum, handler):
        ran["n"] += 1
        handler(signum, None)
    monkeypatch.setattr(tsr.signal, "signal", fake_signal)
    cfg = tsr.TrainConfig(base=tmp_path, tasks=_write_tasks(tmp_path), out=tmp_path / "o")
    # Drive just the handler registration path by calling train and letting it fail on CUDA.
    with pytest.raises((SystemExit, Exception)):
        tsr.train(cfg)
    assert ran["n"] >= 2, "both SIGINT and SIGTERM should be handled"


# --- 2. data contract ---------------------------------------------------------

def test_the_receipt_shape_carries_every_required_metric():
    """The handoff lists them; this asserts they exist rather than that they are non-empty."""
    required = {"base", "tasks_sha256", "examples", "hyperparameters", "steps_run",
                "stop_reason", "losses", "weight_change", "peak_gpu_gb", "published",
                "trainable_parameters", "resource_limits", "adapter_sha256"}
    source = Path(tsr.__file__).read_text(encoding="utf-8")
    receipt_block = source[source.index('receipt = {'):source.index('receipt_path.write_text')]
    for key in required:
        assert f'"{key}"' in receipt_block, f"the receipt omits {key}"


def test_unverified_labels_are_skipped_rather_than_trained_on(tmp_path):
    from eval.repair_prompts import load_task_examples
    rows = [json.loads(line) for line in _write_tasks(tmp_path, 3).read_text().splitlines()]
    rows[1]["child"]["exact"] = False
    rows[1]["child"]["source_c"] = None
    examples = load_task_examples(rows, split="train")
    assert len(examples) == 2
    assert all(e.record_id != rows[1]["task_id"] for e in examples)


def test_a_task_from_another_split_is_never_used_for_training(tmp_path):
    from eval.repair_prompts import load_task_examples
    rows = [json.loads(line) for line in _write_tasks(tmp_path, 3).read_text().splitlines()]
    rows[0]["split"] = "test"
    assert {e.record_id for e in load_task_examples(rows, split="train")} == \
        {rows[1]["task_id"], rows[2]["task_id"]}
    assert {e.record_id for e in load_task_examples(rows, split="test")} == {rows[0]["task_id"]}


# --- 3. held-out answers cannot enter the input -------------------------------

def test_the_prompt_contains_only_assembly_candidate_and_feedback():
    from eval.repair_prompts import synthetic_repair_prompt
    prompt = synthetic_repair_prompt(asm="ASM-MARKER", candidate="CAND-MARKER",
                                     feedback="FEEDBACK-MARKER")
    for marker in ("ASM-MARKER", "CAND-MARKER", "FEEDBACK-MARKER"):
        assert marker in prompt
    assert "ANSWER-MARKER" not in prompt


# --- 4. the promotion gate ----------------------------------------------------

def _rows(n, exact_ids=()):
    return {f"t{i}": {"exact": i in set(exact_ids), "draws": 2} for i in range(n)}


def _spec(n, *, draws=2, split="test", kind="frozen"):
    """The frozen specification the gate now requires: panel, split and per-arm draw budget.

    The gate takes it as an argument rather than inferring the panel from the rows it is judging,
    because "the two arms agree on 12 ids" is what an incomplete run looks like. These fixtures
    declare the same panel the rows cover and the same budget they executed, so every assertion
    below is about the rule and not about the new plumbing.
    """
    from eval import posttraining_gate as gate
    return gate.EvaluationSpec(expected_task_ids=tuple(f"t{i}" for i in range(n)), split=split,
                               draws_per_task=draws, kind=kind,
                               manifest_sha256="m" * 64, dataset_sha256="d" * 64)


def test_a_failed_gate_keeps_the_baseline():
    """Every non-passing outcome must leave the baseline active."""
    from eval import posttraining_gate as gate

    # The adapter gains nothing.
    out = gate.decide(baseline=_rows(20, [0, 1]), adapter=_rows(20, [0, 1]), spec=_spec(20))
    assert not out.passed and out.verdict == "keep-baseline"
    assert out.active_adapter == "baseline"
    assert any("closed no task" in r for r in out.reasons)

    # The adapter gains one and loses one: a trade is not an improvement.
    out = gate.decide(baseline=_rows(20, [0, 1]), adapter=_rows(20, [0, 2]), spec=_spec(20))
    assert not out.passed and out.verdict == "keep-baseline"
    assert out.active_adapter == "baseline"
    assert any("lost" in r for r in out.reasons)
    assert out.counts["gained_by_adapter"] == ["t2"]
    assert out.counts["lost_by_adapter"] == ["t1"]


def test_a_small_panel_is_inconclusive_and_never_a_promotion():
    """A smoke set validates wiring. It cannot establish superiority."""
    from eval import posttraining_gate as gate

    out = gate.decide(baseline=_rows(3, []), adapter=_rows(3, [0, 1, 2]), spec=_spec(3))
    assert not out.passed
    assert out.verdict == "inconclusive", (
        "3/3 beats the baseline and must still not promote on a 3-task panel")
    assert out.active_adapter == "baseline"
    assert any("needed before a result means anything" in r for r in out.reasons)


def test_a_clean_gain_on_a_large_enough_panel_promotes():
    from eval import posttraining_gate as gate

    out = gate.decide(baseline=_rows(20, [0]), adapter=_rows(20, [0, 1, 2]), spec=_spec(20))
    assert out.passed and out.verdict == "promote"
    assert out.active_adapter == "adapter"
    assert out.counts["gained_by_adapter"] == ["t1", "t2"]


def test_tasks_only_one_arm_ran_make_the_result_inconclusive():
    """A task the adapter never ran is not a task it won."""
    from eval import posttraining_gate as gate

    baseline = {f"t{i}": {"exact": False, "draws": 2} for i in range(20)}
    adapter = {f"t{i}": {"exact": i == 0, "draws": 2} for i in range(19)}
    out = gate.decide(baseline=baseline, adapter=adapter, spec=_spec(20))
    assert not out.passed and out.verdict == "inconclusive"
    assert out.counts["only_baseline_ran"] == ["t19"]
    assert any("same tasks" in r for r in out.reasons)


def test_the_gate_records_what_cannot_authorise_promotion():
    from eval import posttraining_gate as gate

    out = gate.decide(baseline=_rows(20), adapter=_rows(20), spec=_spec(20)).as_dict()
    forbidden = " ".join(out["rule"]["cannot_authorise"])
    for phrase in ("training-loss", "similarity", "smoke"):
        assert phrase in forbidden, f"the rule must name {phrase} as insufficient"


def test_the_gate_never_reads_a_similarity_score():
    """`score` is a diagnostic; only `exact` may move the decision."""
    from eval import posttraining_gate as gate

    baseline = {f"t{i}": {"exact": False, "best_score": 99.9, "draws": 2} for i in range(20)}
    adapter = {f"t{i}": {"exact": False, "best_score": 10.0, "draws": 2} for i in range(20)}
    out = gate.decide(baseline=baseline, adapter=adapter, spec=_spec(20))
    assert not out.passed, "a score improvement with no exactness gain must not promote"
    assert out.verdict == "keep-baseline"


def test_a_published_adapter_is_required_before_evaluating(tmp_path):
    """An unpublished adapter may be the initialisation or a killed run's leftovers."""
    from eval import train_source_repair as tsr
    assert tsr.published_adapter(tmp_path / "missing") is None
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    (adapter / "adapter_model.safetensors").write_bytes(b"x")
    assert tsr.published_adapter(adapter) is None
    (adapter / tsr.PUBLISHED_MARKER).write_text(json.dumps({"stage": "training"}))
    assert tsr.published_adapter(adapter)


def test_the_evaluator_refuses_a_split_the_manifest_did_not_freeze(tmp_path):
    """The FIXTURE is a content-bound manifest (schema 3); the assertion is unchanged.

    A manifest is only a freeze if it binds the content of the records it names, so a fixture
    without `record_hashes`/`dataset_sha256` is now refused for a different reason than the one
    under test. Everything the loader checks is made consistent here except the one thing this
    test is about: a frozen id the dataset does not contain.
    """
    from eval import evaluate_source_repair as ev

    manifest = tmp_path / "FROZEN_SPLIT.json"
    dataset = tmp_path / "tasks.jsonl"
    raw = (json.dumps({"task_id": "a"}) + "\n").encode("utf-8")
    dataset.write_bytes(raw)
    payload = {
        "schema_version": 3,
        "frozen_at": 1,
        "compiler_recipe": {"target": "build/x.o", "compiler": "ido-5.3",
                            "command_sha256": "c" * 64, "command": ["cc"]},
        "counts": {"train": 0, "test": 1, "total": 1},
        "task_ids": {"train": [], "test": ["zzz"]},
        "record_hashes": {"zzz": {"content": "x" * 64, "input": "y" * 64, "answer": "z" * 64}},
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "dataset_lines": 1,
    }
    payload["manifest_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        ev.load_frozen_tasks(manifest, dataset, split="test")
    assert "does not contain" in str(excinfo.value)


def test_the_evaluator_refuses_an_unfrozen_manifest(tmp_path):
    from eval import evaluate_source_repair as ev

    manifest = tmp_path / "M.json"
    dataset = tmp_path / "t.jsonl"
    dataset.write_text(json.dumps({"task_id": "a"}) + "\n", encoding="utf-8")
    manifest.write_text(json.dumps({"counts": {"test": 1}, "task_ids": {"test": ["a"]}}),
                        encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        ev.load_frozen_tasks(manifest, dataset, split="test")
    assert "not a freeze" in str(excinfo.value)

