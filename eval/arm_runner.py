"""One runner for model-arm pilots: train -> serve -> examine -> grade -> paired summary, from a JSON spec.

    python3 -m eval.arm_runner SPEC.json            (WSL, sbk1 venv; resumable: finished stages are skipped)

Replaces the per-pilot shell queues (loc_ab.sh, grpo_mt.sh, reading_pilot.sh), each of which re-implemented
launching, waiting and failure handling and twice got it wrong (a launch that died with its session; a watcher
that matched an old FAILED line). Here a stage failure writes OUT/FAILED naming the stage, stops ONLY this
runner's server, and exits non-zero; OUT/done is written last.

SPEC:
{
  "out": "/home/.../pilot-dir",
  "base": "/home/.../models/qwen2.5-coder-7b",
  "arms": {"read": {"tasks": "arms/read.jsonl", "init_adapter": "/.../adapter_mixed"},   # trained here
           "mixed": {"adapter": "/.../adapter_mixed"}},                                 # already trained
  "train": {"max_seq_len": 3072, "max_seconds": 16000, "args": ["--completion-logits"]},
  "exams": [{"name": "explain", "exam": "exam.json", "tasks": "tasks.jsonl", "context": "ctx.jsonl",
             "arms": ["base", "mixed", "read"], "run_args": ["--turns", "1"]}],
  "compare": [["read", "mixed"]],
  "jobs": 4
}
Relative paths resolve against `out`. Training is one epoch (steps = examples / grad-accum 4), as in the logic
pilot. Grading of one arm's answers runs on the CPU while the next arm is examined on the GPU.
"""
from __future__ import annotations

import collections
import concurrent.futures
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRADER_DIR = ROOT / "eval/results/edit-capability-20261002"
DECOMP = Path.home() / "decomp"
PY = DECOMP / "sbk1/.venv/bin/python"
TPY = DECOMP / "train-venv/bin/python"
URL = "http://127.0.0.1:8101/v1/models"


class StageFailed(RuntimeError):
    pass


class Runner:
    def __init__(self, spec_path: Path):
        self.spec = json.loads(spec_path.read_text())
        self.out = Path(self.spec["out"])
        self.out.mkdir(parents=True, exist_ok=True)
        self.server = None

    def path(self, p) -> Path:
        p = Path(p)
        return p if p.is_absolute() else self.out / p

    def log(self, msg: str) -> None:
        line = f"[{time.strftime('%F %T')}] {msg}"
        print(line, flush=True)
        with open(self.out / "runner.log", "a") as f:
            f.write(line + "\n")

    def run(self, stage: str, argv: list, *, python=PY, cwd=ROOT, env=None) -> None:
        log = self.out / f"{stage}.log"
        with open(log, "w") as f:
            r = subprocess.run([str(python), *map(str, argv)], cwd=cwd, stdout=f, stderr=subprocess.STDOUT,
                               env=(os.environ | (env or {})))
        if r.returncode != 0:
            raise StageFailed(f"{stage} exited {r.returncode}; see {log.name}")

    # -- training ------------------------------------------------------------------------------------------------
    def adapter_of(self, arm: str) -> Path | None:
        cfg = self.spec["arms"].get(arm)
        if cfg is None:
            return None
        return self.path(cfg["adapter"]) if "adapter" in cfg else self.out / f"adapter_{arm}"

    def train(self) -> None:
        t = self.spec.get("train", {})
        for arm, cfg in self.spec["arms"].items():
            if "adapter" in cfg:
                if not (self.path(cfg["adapter"]) / "adapter_config.json").exists():
                    raise StageFailed(f"arm {arm}: adapter {cfg['adapter']} does not exist")
                continue
            out = self.out / f"adapter_{arm}"
            if (out / "training_receipt.json").exists():
                continue
            tasks = self.path(cfg["tasks"])
            n = sum(1 for line in open(tasks) if line.strip())
            argv = ["-m", "eval.train_source_repair", "--base", self.spec["base"], "--tasks", tasks, "--out", out,
                    "--max-steps", n // 4, "--max-examples", n, "--max-seconds", t.get("max_seconds", 16000),
                    "--max-seq-len", t.get("max_seq_len", 3072), *t.get("args", ["--completion-logits"])]
            if cfg.get("init_adapter"):
                argv += ["--init-adapter", self.path(cfg["init_adapter"])]
            self.log(f"train {arm}: {n} examples" + (f", from {cfg['init_adapter']}" if cfg.get("init_adapter") else ""))
            self.run(f"train_{arm}", argv, python=TPY, env={"SOLVER_GPU_MEMORY_FRACTION": "0.70"})
            receipt = json.loads((out / "training_receipt.json").read_text())
            if not receipt.get("published"):
                raise StageFailed(f"train {arm}: adapter not published")
            self.log(f"trained {arm}: {receipt.get('steps')} steps, {receipt.get('seconds')} s")

    # -- serving -------------------------------------------------------------------------------------------------
    def start_server(self, arms: list[str]) -> None:
        try:
            with urllib.request.urlopen(URL, timeout=3):
                raise StageFailed("port 8101 already has a server; refusing to take over another process")
        except urllib.error.URLError:
            pass
        cmd = ["bash", str(ROOT / ".cache/recon/serve_lowfootprint.sh")]
        for arm in arms:
            cmd += ["--adapter", f"{arm}={self.adapter_of(arm)}"]
        env = os.environ | {"SOLVER_VLLM_GPU_UTILIZATION": "0.70", "SOLVER_VLLM_MAX_SEQS": str(self.spec.get("jobs", 4)),
                            "PYTHONUNBUFFERED": "1", **self.spec.get("serve_env", {})}
        self.server = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=open(self.out / "serve.log", "w"),
                                       stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(240):
            if self.server.poll() is not None:
                raise StageFailed("server exited during startup; see serve.log")
            try:
                with urllib.request.urlopen(URL, timeout=5) as r:
                    served = {m.get("meta", {}).get("adapter") for m in json.load(r).get("data", [])}
                missing = [a for a in arms if a not in served]
                if missing:
                    raise StageFailed(f"server does not serve {missing}")
                self.log(f"server up with {arms}")
                return
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                time.sleep(5)
        raise StageFailed("server did not start in 20 minutes")

    def stop_server(self) -> None:
        if self.server is not None and self.server.poll() is None:
            os.killpg(self.server.pid, signal.SIGTERM)
            try:
                self.server.wait(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(self.server.pid, signal.SIGKILL)
            for _ in range(30):                         # the next arm's server needs the port and the memory back
                try:
                    urllib.request.urlopen(URL, timeout=2).close()
                    time.sleep(2)
                except (urllib.error.URLError, ConnectionError, TimeoutError):
                    break
            time.sleep(10)
        self.server = None

    # -- exams and grading ---------------------------------------------------------------------------------------
    def grade(self, exam: dict, arm: str) -> None:
        name = exam["name"]
        out = self.out / f"grades_{name}_{arm}.jsonl"
        if out.exists():
            return
        self.run(f"grade_{name}_{arm}", [GRADER_DIR / "logic_grade.py", self.path(exam["tasks"]),
                                         "--context", self.path(exam["context"]), "--exam", self.path(exam["exam"]),
                                         "--answers", self.out / f"answers_{name}_{arm}.jsonl", "--out", out,
                                         "--jobs", 6], cwd=GRADER_DIR)

    def examine(self) -> None:
        # One adapter per server, restarted between arms (about a minute each): serving several LoRA slots at once
        # left no KV cache when the desktop held ~4 GB of the card (reading pilot, 2026-10-04: -1.48 GiB).
        exams = self.spec["exams"]
        order = list(dict.fromkeys(a for e in exams for a in e["arms"]))
        pending = [(e, a) for a in order for e in exams if a in e["arms"]
                   and not (self.out / f"grades_{e['name']}_{a}.jsonl").exists()]
        if not pending:
            return
        grading, futures = concurrent.futures.ThreadPoolExecutor(1), []
        try:
            for arm in order:
                mine = [e for e, a in pending if a == arm]
                need_gpu = [e for e in mine if not self.complete(e, arm)]
                if need_gpu:
                    self.start_server([] if arm == "base" else [arm])
                    for e in need_gpu:
                        self.log(f"exam {e['name']} / {arm}")
                        self.run(f"exam_{e['name']}_{arm}",
                                 ["-m", "eval.logic_exam", "run", self.path(e["exam"]), "--arm", arm,
                                  "--out", self.out / f"answers_{e['name']}_{arm}.jsonl",
                                  "--jobs", self.spec.get("jobs", 4), *e.get("run_args", [])])
                    self.stop_server()
                futures += [grading.submit(self.grade, e, arm) for e in mine]
        finally:
            self.stop_server()
            grading.shutdown(wait=True)
        for f in futures:
            f.result()                                  # a grading failure surfaces here as StageFailed

    def complete(self, exam: dict, arm: str) -> bool:
        answers = self.out / f"answers_{exam['name']}_{arm}.jsonl"
        if not answers.exists():
            return False
        ids = set(json.loads(self.path(exam["exam"]).read_text())["ids"])
        rows = [json.loads(line) for line in answers.read_text().splitlines() if line.strip()]
        return ids <= {r["id"] for r in rows if "error" not in r}

    # -- summary -------------------------------------------------------------------------------------------------
    def summarize(self) -> dict:
        summary = {}
        for e in self.spec["exams"]:
            tasks = {}
            for line in open(self.path(e["tasks"])):
                t = json.loads(line)
                tasks[t["id"]] = t
            per_arm = {}
            for a in e["arms"]:
                grades = {g["id"]: g for g in map(json.loads, open(self.out / f"grades_{e['name']}_{a}.jsonl"))}
                by = collections.Counter()
                for tid, g in grades.items():
                    t = tasks[tid]
                    key = f"{t['split']}/{t['kind']}/{t.get('class', '')}"
                    by[key + "/n"] += 1
                    by[key + "/full"] += bool(g["rows"])
                    if "compiles" in g:                 # decompile regression exam: continuous signal
                        by[key + "/compiles"] += bool(g["compiles"])
                        if g.get("distance") is not None:
                            # distance as a share of the target, so long functions do not dominate
                            by[key + "/distance_pct_sum"] += round(100 * g["distance"] / max(1, g["target_rows"]))
                    if "tooled" in g:                   # after the campaign's deterministic cleanup
                        by[key + "/tooled_compiles"] += bool(g["tooled"]["compiles"])
                        by[key + "/tooled_exact"] += bool(g["tooled"]["exact"])
                    if "lines_ok" in g:
                        by[key + "/lines_ok"] += g["lines_ok"]
                        by[key + "/lines"] += g["lines"]
                per_arm[a] = {"grades": grades, "by": dict(sorted(by.items())),
                              "full": sum(bool(g["rows"]) for g in grades.values()), "n": len(grades)}
            pairs = {}
            for x, y in self.spec.get("compare", []):
                if x in per_arm and y in per_arm:
                    gx, gy = per_arm[x]["grades"], per_arm[y]["grades"]
                    pairs[f"{x} vs {y}"] = {"gained": sum(1 for k in gx if gx[k]["rows"] and not gy[k]["rows"]),
                                            "lost": sum(1 for k in gx if gy[k]["rows"] and not gx[k]["rows"])}
                    both = [k for k in gx if gx[k].get("distance") is not None
                            and gy.get(k, {}).get("distance") is not None]
                    if both or any("compiles" in g for g in gx.values()):
                        # paired, on the tasks where both compiled: no share of non-compiling answers hides here
                        pairs[f"{x} vs {y}"].update(
                            closer=sum(gx[k]["distance"] < gy[k]["distance"] for k in both),
                            farther=sum(gx[k]["distance"] > gy[k]["distance"] for k in both),
                            compiles_gained=sum(1 for k in gx if gx[k].get("compiles") and not gy[k].get("compiles")),
                            compiles_lost=sum(1 for k in gx if gy[k].get("compiles") and not gx[k].get("compiles")))
            summary[e["name"]] = {"arms": {a: {k: v for k, v in r.items() if k != "grades"}
                                           for a, r in per_arm.items()}, "paired": pairs}
        (self.out / "summary.json").write_text(json.dumps(summary, indent=1))
        return summary

    def main(self) -> int:
        (self.out / "FAILED").unlink(missing_ok=True)
        stage = "train"
        try:
            self.train()
            stage = "examine"
            self.examine()
            stage = "summary"
            summary = self.summarize()
        except Exception as exc:
            self.stop_server()
            self.log(f"FAILED in {stage}: {exc}")
            (self.out / "FAILED").write_text(f"{stage}: {exc}\n")
            return 1
        for name, s in summary.items():
            self.log(f"{name}: " + ", ".join(f"{a} {r['full']}/{r['n']}" for a, r in s["arms"].items())
                     + "".join(f"; {k} +{v['gained']} -{v['lost']}" for k, v in s["paired"].items()))
        (self.out / "done").write_text("done\n")
        return 0


if __name__ == "__main__":
    sys.exit(Runner(Path(sys.argv[1])).main())
