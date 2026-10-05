"""eval/arm_runner.py: fail-closed stages, resume detection, and the paired summary."""
import json

from eval import arm_runner


def _spec(tmp_path, **extra):
    spec = {"out": str(tmp_path / "out"), "base": "/nonexistent", "arms": {}, "exams": [], **extra}
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec))
    return path


def _jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_a_missing_adapter_fails_closed_and_names_the_stage(tmp_path):
    runner = arm_runner.Runner(_spec(tmp_path, arms={"mixed": {"adapter": str(tmp_path / "nope")}}))
    assert runner.main() == 1
    assert (tmp_path / "out/FAILED").read_text().startswith("train:")
    assert not (tmp_path / "out/done").exists()


def test_an_exam_with_an_error_row_is_not_complete(tmp_path):
    runner = arm_runner.Runner(_spec(tmp_path))
    (tmp_path / "exam.json").write_text(json.dumps({"ids": ["a", "b"]}))
    exam = {"name": "x", "exam": str(tmp_path / "exam.json")}
    _jsonl(tmp_path / "out/answers_x_m.jsonl", [{"id": "a", "answer": ""}, {"id": "b", "error": "timeout"}])
    assert not runner.complete(exam, "m")
    _jsonl(tmp_path / "out/answers_x_m.jsonl", [{"id": "a", "answer": ""}, {"id": "b", "answer": ""}])
    assert runner.complete(exam, "m")


def test_summary_counts_by_class_and_pairs_arms(tmp_path):
    tasks = [{"id": f"t{i}", "split": "exam", "kind": "logic-explain", "class": "drop_stmt"} for i in range(3)]
    _jsonl(tmp_path / "tasks.jsonl", tasks)
    exam = {"name": "e", "tasks": str(tmp_path / "tasks.jsonl"), "arms": ["a", "b"]}
    runner = arm_runner.Runner(_spec(tmp_path, exams=[exam], compare=[["a", "b"]]))
    _jsonl(tmp_path / "out/grades_e_a.jsonl", [{"id": "t0", "rows": True}, {"id": "t1", "rows": True},
                                               {"id": "t2", "rows": False}])
    _jsonl(tmp_path / "out/grades_e_b.jsonl", [{"id": "t0", "rows": True}, {"id": "t1", "rows": False},
                                               {"id": "t2", "rows": True}])
    s = runner.summarize()["e"]
    assert s["arms"]["a"]["full"] == 2 and s["arms"]["a"]["by"]["exam/logic-explain/drop_stmt/full"] == 2
    assert s["paired"]["a vs b"] == {"gained": 1, "lost": 1}
