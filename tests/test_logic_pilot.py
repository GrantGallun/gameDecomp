"""Guard the pilot's training budget and compiler-logic answer protocol."""
import importlib.util
import json
from pathlib import Path

import pytest

from eval import repair_prompts as rp
from eval import train_source_repair as trainer


def experiment_module(name):
    path = Path(__file__).resolve().parents[1] / "eval/results/edit-capability-20261002" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CharacterTokenizer:
    eos_token_id = 0

    def apply_chat_template(self, messages, **kwargs):
        return [1, 2] + list(map(ord, messages[0]["content"])) + [3]

    def __call__(self, text, **kwargs):
        return {"input_ids": list(map(ord, text))}


@pytest.mark.parametrize("kind,answer", [(rp.LOGIC_PREDICT_KIND, "SAME"),
                                        (rp.LOGIC_NEED_KIND, "NEED: x"),
                                        (rp.LOGIC_EXPLAIN_KIND, "DELETE 2")])
def test_logic_training_completion_uses_the_exam_protocol(kind, answer):
    example = rp.Example(kind=kind, prompt="question", completion=answer, function="f", record_id="x")
    ids, boundary = trainer.render(CharacterTokenizer(), example)
    assert "".join(map(chr, ids[boundary:-1])) == answer
    assert ids[-1] == 0


def test_source_repair_keeps_its_c_prefill():
    example = rp.Example(kind=rp.SYNTHETIC_KIND, prompt="q", completion="int f();", function="f", record_id="x")
    ids, boundary = trainer.render(CharacterTokenizer(), example)
    assert "".join(map(chr, ids[boundary:-1])) == "```c\nint f();"


def test_arm_counts_match_after_length_filtering_and_have_whole_batches():
    arms_module = experiment_module("logic_arms")
    tasks = []
    for kind in (rp.LOGIC_EXPLAIN_KIND, rp.LOGIC_PREDICT_KIND):
        for n in range(7):
            tasks.append({"id": f"{kind}:{n}", "kind": kind, "split": "train",
                          "prompt_version": rp.LOGIC_PROMPT_VERSION, "prompt": "q" * (100 if n == 0 else 1),
                          "completion": "SAME", "variant": "base"})
    tasks.append(dict(tasks[-1], id="heldout", split="exam"))
    arms, receipt = arms_module.build_arms(tasks, CharacterTokenizer(), cap=7, max_seq_len=20, grad_accum=4)
    assert {k: len(v) for k, v in arms.items()} == {"repair": 4, "logic": 4, "mixed": 4}
    assert receipt["dropped_for_length"] == 2
    assert all(t["split"] == "train" and not t["id"].endswith(":0") for rows in arms.values() for t in rows)
    assert [t["kind"] for t in arms["mixed"]].count(rp.LOGIC_EXPLAIN_KIND) == 2


def test_grader_accepts_blank_lines_without_discarding_instruction_rows():
    grader = experiment_module("logic_grade")
    answer = "Here: **DIFFER**\n\n```asm\n- lw   v0,0(a0)\n\n+lh v0,0(a0)\n```"
    assert grader.grade_predict("DIFFER\n-lw v0,0(a0)\n+lh v0,0(a0)", answer) == {"label": True, "rows": True}


@pytest.mark.parametrize("preamble", ["The answer is not SAME.", "I need to choose SAME or DIFFER."])
def test_grader_reads_the_verdict_not_words_in_prefatory_prose(preamble):
    grader = experiment_module("logic_grade")
    answer = preamble + "\nDIFFER\n-lw v0,0(a0)\n+lh v0,0(a0)"
    assert grader.grade_predict("DIFFER\n-lw v0,0(a0)\n+lh v0,0(a0)", answer)["rows"]
    assert not grader.grade_predict("SAME", answer)["rows"]


def test_grader_rejects_contradictory_verdict_lines():
    grader = experiment_module("logic_grade")
    assert not grader.grade_predict("SAME", "SAME\nDIFFER")["rows"]


def test_explain_script_preserves_c_double_dereference():
    grader = experiment_module("logic_grade")
    assert grader.extract_script("```text\nREPLACE 2: **p = 3;\n```") == "REPLACE 2: **p = 3;"


def test_grader_does_not_count_missing_exam_answers_as_model_failures():
    grader = experiment_module("logic_grade")
    tasks = [{"id": "a"}, {"id": "b"}]
    with pytest.raises(ValueError, match="missing"):
        grader.validate_answers(tasks, [{"id": "a", "answer": "SAME"}])


def test_grader_rejects_duplicate_answers():
    grader = experiment_module("logic_grade")
    with pytest.raises(ValueError, match="duplicate"):
        grader.validate_answers([{"id": "a"}], [{"id": "a", "answer": "SAME"}] * 2)


def test_pilot_refuses_a_published_but_short_training_run():
    pilot = experiment_module("logic_pilot")
    receipt = {"examples": {"kept": 8, "dropped_for_length": 0}, "steps_run": 1, "published": True}
    with pytest.raises(ValueError, match="steps"):
        pilot.validate_training(receipt, 8)


def test_pilot_refuses_unequal_admitted_training_examples():
    pilot = experiment_module("logic_pilot")
    receipt = {"examples": {"kept": 7, "dropped_for_length": 1}, "steps_run": 2, "published": True}
    with pytest.raises(ValueError, match="examples"):
        pilot.validate_training(receipt, 8)


def test_pilot_refuses_same_count_context_with_a_changed_digest(tmp_path):
    pilot = experiment_module("logic_pilot")
    context = tmp_path / "context.jsonl"
    context.write_text('{"id":"changed"}\n')
    with pytest.raises(ValueError, match="digest"):
        pilot.validate_context(context, {"rows_admitted": 1, "output_sha256": "old",
                                         "functions": 1, "total_groups": 1})


def test_pilot_checks_adapters_by_server_metadata_not_bare_model_ids():
    pilot = experiment_module("logic_pilot")
    models = {"data": [{"id": "qwen+mixed", "meta": {"adapter": "mixed"}}]}
    pilot.validate_models(models, ["mixed"])
    with pytest.raises(ValueError, match="adapters"):
        pilot.validate_models(models, ["repair", "mixed"])


def test_exam_logs_inference_failures_and_reports_incomplete(tmp_path, monkeypatch):
    from eval import logic_exam
    from tools.lora_serve import client

    class FailedClient:
        def __init__(self, *args, **kwargs):
            pass

        def chat(self, *args, **kwargs):
            raise RuntimeError("service disconnected")

    monkeypatch.setattr(client, "InferenceClient", FailedClient)
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text(json.dumps({"id": "x", "split": "exam", "prompt": "q", "kind": "logic-predict"}) + "\n")
    exam = tmp_path / "exam.json"
    logic_exam.freeze(tasks, exam, ["exam"], 600, 9000)
    out = tmp_path / "answers.jsonl"
    result = logic_exam.run(exam, "base", out, "http://unused", 1)
    assert result["complete"] is False
    assert result["errors"] == 1
    answer = json.loads(out.read_text())
    assert answer["id"] == "x" and "service disconnected" in answer["error"]


def test_read_answer_is_the_statement_without_markup():
    grader = experiment_module("logic_grade")
    assert grader.read_statement("```c\nLine 7: p->x = 3;\n```") == "p->x = 3;"
    assert grader.read_statement("  7| f(a, &b);") == "f(a, &b);"


def test_read_grader_substitutes_at_the_marker_and_compiles(monkeypatch):
    """It must FIRE on its own completion and refuse a wrong statement: the compile decides, not the text."""
    import sys
    import types
    grader = experiment_module("logic_grade")
    compiled = {}
    fake = types.SimpleNamespace(
        compile_tu=lambda build, text, tag: compiled.setdefault("text", text) and Path("x.o"),
        listing=lambda obj, name: ["sw a1,0x24(a0)"] if "p->speed = v;" in compiled["text"] else ["jr ra"])
    fake.compile_row = lambda row, text, tag, bmap=None, extra=(): fake.compile_tu(None, text, tag)
    monkeypatch.setitem(sys.modules, "public_plant", fake)
    row = {"perturbed_fn": "void f(S *p, int v)\n{\n  ; /* ? */\n}\n", "marker_line": 3, "context": "typedef int S;",
           "repository": "r", "variant": "v", "file": "f.c", "function": "f", "target": ["sw a1,0x24(a0)"]}
    bmap = {("r.v", "f.c"): {}}
    task = {"row_id": "x", "kind": rp.LOGIC_READ_KIND}
    assert grader.grade(task, "p->speed = v;", {"x": row}, bmap)["rows"]
    assert "  p->speed = v;" in compiled.pop("text")
    assert not grader.grade(task, "p->speed = 0;", {"x": row}, bmap)["rows"]


def test_read_tasks_are_trainable_logic_kinds():
    assert rp.LOGIC_READ_KIND in rp.LOGIC_KINDS
    prompt = rp.logic_read_prompt(compiler="IDO 5.3", opt="-O2", context="int g;", numbered="  1| ; /* ? */",
                                  line=1, listing="> sw a1,0(a0)")
    assert "line 1" in prompt and "> sw a1,0(a0)" in prompt and "int g;" in prompt


def test_read_answer_keeps_c_double_pointers():
    """Self-check found it: markdown cleanup deleted `**` from `(struct Animation **) p` (2 of 7,604 answers)."""
    grader = experiment_module("logic_grade")
    assert grader.read_statement("q = (T **) p;") == "q = (T **) p;"
    assert grader.read_statement("`q = (T **) p;`") == "q = (T **) p;"


def test_multi_read_grades_each_blank_alone_and_all_together(monkeypatch):
    import sys
    import types
    grader = experiment_module("logic_grade")
    fn = "void f(S *p)\n{\n  ; /* ? */\n  ; /* ? */\n}\n"

    def listing(obj, name):
        text = fake.last
        return [r for stmt, r in (("p->a = 1;", "sw a"), ("g(p);", "jal g")) if stmt in text] or ["jr ra"]
    fake = types.SimpleNamespace(last="", listing=listing)
    fake.compile_tu = lambda build, text, tag: (setattr(fake, "last", text), Path("x.o"))[1]
    fake.compile_row = lambda row, text, tag, bmap=None, extra=(): fake.compile_tu(None, text, tag)
    monkeypatch.setitem(sys.modules, "public_plant", fake)
    row = {"perturbed_fn": fn, "marker_lines": [3, 4], "context": "", "repository": "r", "variant": "v",
           "file": "f.c", "function": "f", "target": ["sw a", "jal g"],
           "partial_targets": {"3": ["sw a"], "4": ["jal g"]}}
    bmap = {("r.v", "f.c"): {}}
    task = {"row_id": "x", "kind": rp.LOGIC_READ_KIND}
    full = grader.grade(task, "Line 3: p->a = 1;\nLine 4: g(p);", {"x": row}, bmap)
    assert full["rows"] and full["lines_ok"] == 2
    half = grader.grade(task, "Line 3: p->a = 1;\nLine 4: h(p);", {"x": row}, bmap)
    assert not half["rows"] and half["lines_ok"] == 1


def test_decompile_answer_is_the_fenced_function_or_the_whole_text():
    grader = experiment_module("logic_grade")
    assert grader.decompiled_code("Here:\n```c\nint f(void) { return 1; }\n```\nDone.") == "int f(void) { return 1; }"
    assert grader.decompiled_code("int f(void) { return **p; }") == "int f(void) { return **p; }"


def test_tooled_decompile_keeps_only_the_target_definition_in_c89():
    grader = experiment_module("logic_grade")
    answer = ("#include <stdint.h>\nstruct S { int a; };\nvoid g(void);\n"
              "static void f(uint8_t x) {\n    register int t asm(\"t0\") = x;\n    g();\n}\n")
    assert grader.function_definition(answer, "f").startswith("static void f(uint8_t x)")
    tooled = grader.tooled_code(answer, "f")
    assert "#include" not in tooled and "struct S" not in tooled and "static" not in tooled
    assert "asm(" not in tooled and "uint8_t" not in tooled and "g();" in tooled


def test_self_curriculum_weak_spots_come_from_train_practice_only(tmp_path):
    sc = experiment_module("self_curriculum")
    tasks = tmp_path / "t.jsonl"
    tasks.write_text("".join(json.dumps({"id": f"e{i}", "split": "train", "kind": "logic-explain",
                                          "class": "drop_stmt" if i < 10 else "const"}) + "\n" for i in range(20))
                     + json.dumps({"id": "x", "split": "exam", "kind": "logic-explain", "class": "const"}) + "\n")
    grades = tmp_path / "g.jsonl"
    grades.write_text("".join(json.dumps({"id": f"e{i}", "rows": i % 10 >= 8}) + "\n" for i in range(10))
                      + "".join(json.dumps({"id": f"e{i}", "rows": True}) + "\n" for i in range(10, 20)))
    profile = sc.weak([grades], [tasks], tmp_path / "out")
    assert profile["drop_stmt"]["fail_rate"] == 0.8 and profile["const"]["fail_rate"] == 0.0
    assert profile["drop_stmt"]["weight"] == 1.0                 # all generation goes where the model fails
    leaked = tmp_path / "leak.jsonl"
    leaked.write_text(json.dumps({"id": "x", "rows": False}) + "\n")
    with pytest.raises(SystemExit, match="train-split practice only"):
        sc.weak([leaked], [tasks], tmp_path / "out2")


def test_self_curriculum_keeps_only_learnable_tasks():
    sc = experiment_module("self_curriculum")
    assert [sc.learnable(s, 4) for s in range(5)] == [False, True, True, True, False]


def test_examiner_is_paid_for_hard_but_fair_tasks_only():
    sc = experiment_module("self_curriculum")
    assert sc.examiner_reward(0, 4) == 0.0        # never solved: impossible or broken earns nothing
    assert sc.examiner_reward(4, 4) == 0.0        # always solved: trivial earns nothing
    assert sc.examiner_reward(1, 4) > sc.examiner_reward(3, 4) > 0
    assert rp.EXAMINER_KIND in rp.LOGIC_KINDS
