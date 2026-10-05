"""Resume logic-pilot-v3 after the train_logic crash (CUDA "unknown error", 2026-10-03 23:59 UTC).

    python3 resume_logic_pilot.py [--out DIR]

Same experiment, not a rerun: refuses unless every pilot source file, the tasks file and the frozen exam match the
launch receipt (prerequisites.json), and the finished stages are complete (base and base_fs answers cover every
frozen id with no request error; the repair adapter is published with the matched budget). The crashed logic run's
directory and log are moved, never deleted, into failed-<time>/. Then: train logic and mixed with the launch's exact
arguments, serve all three adapters, exam, grade, compare -- logic_pilot.Pilot's own stages, unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path

from logic_pilot import BASE, HERE, PUBLIC, ROOT, TPY, Pilot, sha, validate_context, validate_training


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path,
                    default=Path.home() / "decomp/experiments/edit-capability-20261002/logic-pilot-v3")
    ap.add_argument("--context", type=Path, default=PUBLIC / "context-v3.jsonl")
    a = ap.parse_args()
    out = a.out
    pre = json.loads((out / "prerequisites.json").read_text())
    changed = [p for p, d in pre["source_sha256"].items() if sha(ROOT / p) != d]
    if changed:
        raise SystemExit(f"pilot sources changed since launch, refusing a mixed-code resume: {changed}")
    tasks, exam = out / "logic-v3/tasks.jsonl", out / "exam.json"
    if sha(tasks) != pre["tasks_sha256"] or sha(a.context) != pre["context_sha256"]:
        raise SystemExit("tasks or context changed since launch")
    ids = set(json.loads(exam.read_text())["ids"])
    for arm in ("base", "base_fs"):
        rows = [json.loads(line) for line in (out / f"answers_{arm}.jsonl").read_text().splitlines()]
        if {r["id"] for r in rows} & ids != ids or any(r.get("error") for r in rows):
            raise SystemExit(f"{arm} answers are incomplete or carry request errors")
    n = pre["n"]
    validate_training(json.loads((out / "adapter_repair/training_receipt.json").read_text()), n)

    failed = out / f"failed-{time.strftime('%Y%m%d-%H%M%S')}"
    for name in ("adapter_logic", "adapter_mixed", "train_logic.log", "train_mixed.log", "status.json"):
        if (out / name).exists() and not (out / name / "PUBLISHED.json").exists():
            failed.mkdir(exist_ok=True)
            shutil.move(str(out / name), str(failed / name))

    pilot = Pilot(out)
    if pilot.sources != pre["source_sha256"]:
        raise SystemExit("pilot source digests differ from the launch receipt")
    arms = ("repair", "logic", "mixed")
    try:
        for arm in arms:
            receipt_path = out / f"adapter_{arm}/training_receipt.json"
            if (out / f"adapter_{arm}/PUBLISHED.json").exists():
                validate_training(json.loads(receipt_path.read_text()), n)
                continue
            pilot.run(f"train_{arm}", ["-m", "eval.train_source_repair", "--base", BASE,
                      "--tasks", out / f"arms/{arm}.jsonl", "--out", out / f"adapter_{arm}",
                      "--max-steps", n // 4, "--max-examples", n, "--max-seconds", "9000",
                      "--max-seq-len", "3072", "--completion-logits"], python=TPY,
                      env=os.environ | {"SOLVER_GPU_MEMORY_FRACTION": "0.70"})
            validate_training(json.loads(receipt_path.read_text()), n)
        with pilot.server(arms):
            for arm in arms:
                pilot.run(f"exam_{arm}", ["-m", "eval.logic_exam", "run", exam, "--arm", arm,
                                          "--out", out / f"answers_{arm}.jsonl", "--jobs", "4"])
        context_receipt = json.loads(a.context.with_suffix(".receipt.json").read_text())
        for arm in ("base", "base_fs", *arms):
            validate_context(a.context, context_receipt)
            pilot.run(f"grade_{arm}", [HERE / "logic_grade.py", tasks, "--context", a.context,
                      "--answers", out / f"answers_{arm}.jsonl", "--exam", exam,
                      "--out", out / f"grades_{arm}.jsonl", "--jobs", "6"])
        for metric in ("rows", "label"):
            pilot.run(f"compare_{metric}", [HERE / "logic_compare.py", out / "grades_base.jsonl",
                      *[out / f"grades_{x}.jsonl" for x in ("base_fs", *arms)], "--metric", metric])
        pilot.status("complete", n=n, exam_tasks=len(ids), resumed_from=str(failed))
    except Exception as exc:
        pilot.status("failed", failed_stage=pilot.stage, error=str(exc), resumed=True)
        raise


if __name__ == "__main__":
    main()
