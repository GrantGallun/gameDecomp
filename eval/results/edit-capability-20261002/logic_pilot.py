"""Bounded unattended pilot; stages fail closed and only its own server is stopped.

Run under WSL, optionally --after-pid PID for the existing context builder. Waiting
uses a process-exit event, never a sleep/poll loop. See LOGIC_PILOT.md.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import threading
import time
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DECOMP = Path.home() / "decomp"
PY = DECOMP / "sbk1/.venv/bin/python"
TPY = DECOMP / "train-venv/bin/python"
BASE = DECOMP / "models/qwen2.5-coder-7b"
PUBLIC = DECOMP / "experiments/edit-capability-20261002/public"


def validate_training(receipt, n):
    examples = receipt["examples"]
    if examples["kept"] != n or examples["dropped_for_length"]:
        raise ValueError("training examples differ from the frozen matched budget")
    if receipt["steps_run"] != n // 4:
        raise ValueError("training steps differ from the frozen matched budget")
    if not receipt["published"]:
        raise ValueError("training did not publish an adapter")


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def validate_context(context, receipt):
    if sha(context) != receipt.get("output_sha256"):
        raise ValueError("context digest does not match its completion receipt")
    with context.open() as f:
        count = sum(1 for _ in f)
    if count != receipt["rows_admitted"] or not count:
        raise ValueError("context export is incomplete")
    if receipt.get("functions") != receipt.get("total_groups") or not receipt.get("functions"):
        raise ValueError("context function groups are incomplete")


def validate_context_sources(context, receipt):
    from recover_context import validate_source_rows
    originals = []
    for filename, digest in receipt["inputs"].items():
        path = Path(filename)
        if sha(path) != digest:
            raise ValueError("context source inputs changed after recovery")
        originals.extend(json.loads(line) for line in path.read_text().splitlines())
    with context.open() as f:
        validate_source_rows((json.loads(line) for line in f), originals)


def validate_models(models, adapters):
    loaded = {m.get("meta", {}).get("adapter") for m in models["data"]}
    if not set(adapters) <= loaded:
        raise ValueError("server did not load all requested adapters")


def wait_for_builder(pid):
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
        if b"context_tasks.py" not in cmd and b"recover_context.py" not in cmd:
            raise ValueError(f"PID {pid} is not the expected context builder")
        if not select.select([fd], [], [], 6 * 3600)[0]:
            raise TimeoutError("context builder exceeded the six-hour dependency deadline")
    finally:
        os.close(fd)


class Pilot:
    def __init__(self, out):
        self.out = out
        self.stage = "starting"
        self.sources = {str(p.relative_to(ROOT)): sha(p) for p in [
            HERE / "logic_pilot.py", HERE / "logic_arms.py", HERE / "logic_grade.py", HERE / "logic_compare.py",
            ROOT / "eval/logic_tasks.py", ROOT / "eval/logic_exam.py", ROOT / "eval/repair_prompts.py",
            ROOT / "eval/train_source_repair.py", HERE / "public_plant.py", HERE / "context_tasks.py",
            HERE / "recover_context.py"]}

    def status(self, stage, **extra):
        self.stage = stage
        row = {"stage": stage, "updated_at": time.time(), "pid": os.getpid(), **extra}
        temp = self.out / "status.tmp"
        temp.write_text(json.dumps(row, indent=2))
        temp.replace(self.out / "status.json")
        print(json.dumps(row), flush=True)

    def run(self, stage, argv, *, python=PY, cwd=ROOT, env=None):
        self.status(stage)
        if any(sha(ROOT / p) != digest for p, digest in self.sources.items()):
            raise ValueError("pilot source changed after launch; refusing a mixed-code experiment")
        command = [str(python), *map(str, argv)]
        with (self.out / f"{stage}.log").open("w") as log:
            log.write(json.dumps({"command": command, "cwd": str(cwd)}) + "\n")
            log.flush()
            subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, check=True, env=env)

    @contextlib.contextmanager
    def server(self, adapters=()):
        url = "http://127.0.0.1:8101/v1/models"
        try:
            with urllib.request.urlopen(url, timeout=3):
                raise RuntimeError("port 8101 already has a server; refusing to take over another process")
        except urllib.error.URLError:
            pass
        self.status("server_start", adapters=list(adapters))
        env = os.environ | {"SOLVER_VLLM_GPU_UTILIZATION": "0.70", "SOLVER_VLLM_MAX_SEQS": "4",
                           "PYTHONUNBUFFERED": "1"}
        cmd = ["bash", str(ROOT / ".cache/recon/serve_lowfootprint.sh")]
        for arm in adapters:
            cmd += ["--adapter", f"{arm}={self.out / ('adapter_' + arm)}"]
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, start_new_session=True)
        ready = threading.Event()
        seen = []

        def drain():
            with (self.out / f"serve_{proc.pid}.log").open("w") as log:
                for line in proc.stdout:
                    log.write(line)
                    log.flush()
                    if "listening on http://" in line:
                        seen.append(True)
                        ready.set()
            ready.set()  # startup exit is a failure, not a 20-minute timeout

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            if not ready.wait(1200) or not seen or proc.poll() is not None:
                raise RuntimeError(f"server failed to start; see serve_{proc.pid}.log")
            with urllib.request.urlopen(url, timeout=10) as response:
                models = json.load(response)
            validate_models(models, adapters)
            (self.out / f"serve_{proc.pid}.models.json").write_text(json.dumps(models, indent=2))
            yield
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=15)
            reader.join(timeout=5)
            proc.stdout.close()

    def execute(self, after_pid, cap, context):
        self.status("waiting_for_context", builder_pid=after_pid)
        if after_pid:
            wait_for_builder(after_pid)
        context_receipt = json.loads(context.with_suffix(".receipt.json").read_text())
        validate_context(context, context_receipt)
        validate_context_sources(context, context_receipt)
        tasks = self.out / "logic-v3/tasks.jsonl"
        self.run("export", ["-m", "eval.logic_tasks", context, "--out", tasks.parent, "--holdout-repo", "sm64"])
        self.run("selfcheck", [HERE / "logic_grade.py", tasks, "--context", context, "--self-check",
                               "--out", self.out / "selfcheck.jsonl", "--jobs", "6"])
        self.run("arms", [HERE / "logic_arms.py", tasks, "--out", self.out / "arms", "--base", BASE,
                          "--cap", cap], python=TPY)
        manifest = json.loads((self.out / "arms/arms.json").read_text())
        n = manifest["n"]
        exam = self.out / "exam.json"
        self.run("freeze", ["-m", "eval.logic_exam", "freeze", tasks, "--out", exam])
        frozen = json.loads(exam.read_text())
        all_tasks = [json.loads(line) for line in tasks.read_text().splitlines()]
        train_groups = {t["split_group"] for t in all_tasks if t["split"] == "train"}
        exam_groups = {t["split_group"] for t in all_tasks if t["id"] in set(frozen["ids"])}
        if not frozen["ids"] or train_groups & exam_groups or None in train_groups | exam_groups:
            raise ValueError("empty exam or train/evaluation file-group overlap")
        receipt = {"tasks_sha256": sha(tasks), "context_sha256": sha(context), "source_sha256": self.sources,
                   "n": n, "exam_tasks": len(frozen["ids"]), "split_group_overlap": 0}
        (self.out / "prerequisites.json").write_text(json.dumps(receipt, indent=2))

        def exam_arm(arm):
            self.run(f"exam_{arm}", ["-m", "eval.logic_exam", "run", exam, "--arm", arm,
                                     "--out", self.out / f"answers_{arm}.jsonl", "--jobs", "4"])

        with self.server():
            exam_arm("base")
            exam_arm("base_fs")
        arms = ("repair", "logic", "mixed")
        for arm in arms:
            self.run(f"train_{arm}", ["-m", "eval.train_source_repair", "--base", BASE,
                     "--tasks", self.out / f"arms/{arm}.jsonl", "--out", self.out / f"adapter_{arm}",
                     "--max-steps", n // 4, "--max-examples", n, "--max-seconds", "9000",
                     "--max-seq-len", "3072", "--completion-logits"], python=TPY,
                     env=os.environ | {"SOLVER_GPU_MEMORY_FRACTION": "0.70"})
            trained = json.loads((self.out / f"adapter_{arm}/training_receipt.json").read_text())
            validate_training(trained, n)
        with self.server(arms):
            for arm in arms:
                exam_arm(arm)
        for arm in ("base", "base_fs", *arms):
            validate_context(context, context_receipt)
            self.run(f"grade_{arm}", [HERE / "logic_grade.py", tasks, "--context", context,
                     "--answers", self.out / f"answers_{arm}.jsonl", "--exam", exam,
                     "--out", self.out / f"grades_{arm}.jsonl", "--jobs", "6"])
        for metric in ("rows", "label"):
            self.run(f"compare_{metric}", [HERE / "logic_compare.py", self.out / "grades_base.jsonl",
                     *[self.out / f"grades_{a}.jsonl" for a in ("base_fs", *arms)], "--metric", metric])
        self.status("complete", n=n, exam_tasks=len(frozen["ids"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--after-pid", type=int)
    ap.add_argument("--cap", type=int, default=2400)
    ap.add_argument("--context", type=Path, default=PUBLIC / "context-v3.jsonl")
    ap.add_argument("--out", type=Path, default=DECOMP / "experiments/edit-capability-20261002/logic-pilot-v3")
    args = ap.parse_args()
    if args.out.exists() and any(args.out.iterdir()):
        raise SystemExit("pilot output is not empty: no automatic reruns or budget resets")
    args.out.mkdir(parents=True, exist_ok=True)
    pilot = Pilot(args.out)
    try:
        pilot.execute(args.after_pid, args.cap, args.context)
    except Exception as exc:
        failed_stage = pilot.stage
        pilot.status("failed", failed_stage=failed_stage, error=str(exc))
        raise


if __name__ == "__main__":
    main()
