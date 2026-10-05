"""One-off two-step integration smoke before the full preregistered pilot."""
import json
import os
from pathlib import Path
import select
import signal

from logic_pilot import BASE, HERE, ROOT, PY, TPY, Pilot


def stop_previous(pid, expected):
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
        if expected.encode() not in cmd:
            raise ValueError(f"PID {pid} is no longer the expected smoke helper")
        os.kill(pid, signal.SIGTERM)
        if not select.select([fd], [], [], 90)[0]:
            raise TimeoutError(f"previous helper {pid} did not stop")
        print(f"stopped previous helper {pid}", flush=True)
    finally:
        os.close(fd)


def main():
    # Exact PIDs observed in the pasted-session continuation; never a broad pgrep/kill.
    stop_previous(2653685, "public/run_export.sh")
    stop_previous(3000213, "tools.lora_serve.server")
    original = Path.home() / "decomp/experiments/edit-capability-20261002/logic-pilot-v3-smoke"
    out = original.with_name("logic-pilot-v3-smoke-completion-logits")
    out.mkdir(exist_ok=False)
    pilot = Pilot(out)
    pilot.run("train_mixed", ["-m", "eval.train_source_repair", "--base", BASE,
              "--tasks", original / "arms/mixed.jsonl", "--out", out / "adapter_mixed",
              "--max-steps", "2", "--max-examples", "8", "--max-seconds", "900", "--completion-logits"], python=TPY,
              env=os.environ | {"SOLVER_GPU_MEMORY_FRACTION": "0.70"})
    from logic_pilot import validate_training
    receipt = json.loads((out / "adapter_mixed/training_receipt.json").read_text())
    validate_training(receipt, 8)
    pilot.run("freeze", ["-m", "eval.logic_exam", "freeze", original / "arms/mixed.jsonl",
                         "--out", out / "exam.json", "--splits", "train"])
    with pilot.server(["mixed"]):
        pilot.run("exam_mixed", ["-m", "eval.logic_exam", "run", out / "exam.json", "--arm", "mixed",
                                  "--out", out / "answers_mixed.jsonl", "--jobs", "4"])
    pilot.run("grade_mixed", [HERE / "logic_grade.py", original / "arms/mixed.jsonl",
              "--answers", out / "answers_mixed.jsonl", "--exam", out / "exam.json",
              "--context", out.parent / "public/ctx_smoke.jsonl", "--out", out / "grades_mixed.jsonl", "--jobs", "4"])
    pilot.status("complete", note="8 training-overlap smoke items; plumbing check, not a capability score")


if __name__ == "__main__":
    main()
