"""Run a model arm on a frozen set of compiler-logic tasks through tools.lora_serve (base or a named LoRA adapter).

    python3 -m eval.logic_exam freeze TASKS.jsonl --out EXAM.json [--splits exam,check] [--check-sample 600]
                                      [--max-prompt-chars 9000]
    python3 -m eval.logic_exam run EXAM.json --arm base|<adapter name> --out ANSWERS.jsonl [--url ...] [--jobs 6]
    then: eval/results/edit-capability-20261002/logic_grade.py TASKS.jsonl --answers ANSWERS.jsonl

`freeze` fixes WHICH tasks every arm answers (ids + the tasks file's sha256), so arms are compared on the same items;
an arm whose answers do not cover the frozen ids is incomplete, not "scored lower". Prompts beyond the character cap
are left out at freeze time for every arm alike, and counted. Decoding is greedy (temperature 0, fixed seed):
the exam measures what the arm knows, not sampling luck. `run` is resumable (ids already answered are skipped).
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import hashlib
import json
import random
import sys
from pathlib import Path


def freeze(tasks_path: Path, out: Path, splits: list[str], check_sample: int, max_chars: int,
           kinds: list[str] | None = None, max_tokens: int = 512, train_sample: int = 0,
           exclude: set[str] | None = None) -> dict:
    """`kinds` limits the exam to those task kinds (e.g. logic-explain for a repair pilot); None = every kind.
    A TRAIN-split freeze is a practice set (self_curriculum.py weak spots), never a score: `train_sample` samples it
    and `exclude` drops ids an adapter trained on, so practice is not inflated by memorised items."""
    data = tasks_path.read_bytes()
    tasks = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    chosen, skipped = [], collections.Counter()
    for split in splits:
        pool = [t for t in tasks if t["split"] == split and (not kinds or t["kind"] in kinds)
                and t["id"] not in (exclude or set())]
        if split == "check" and check_sample and len(pool) > check_sample:
            pool = random.Random(f"freeze:{split}").sample(pool, check_sample)
        if split == "train" and train_sample and len(pool) > train_sample:
            pool = random.Random(f"freeze:{split}").sample(pool, train_sample)
        for t in pool:
            if len(t["prompt"]) > max_chars:
                skipped[f"{split}/too-long"] += 1
                continue
            chosen.append(t["id"])
    exam = {"tasks": str(tasks_path), "tasks_sha256": hashlib.sha256(data).hexdigest(), "ids": sorted(chosen),
            "splits": splits, "kinds": kinds, "check_sample": check_sample, "train_sample": train_sample,
            "excluded": len(exclude or ()), "max_prompt_chars": max_chars,
            "skipped": dict(skipped), "decoding": {"temperature": 0.0, "seed": 20261003, "max_tokens": max_tokens}}
    if out.exists():
        raise SystemExit(f"{out} exists: a frozen exam is never overwritten")
    out.write_text(json.dumps(exam, indent=1))
    return {k: (len(v) if k == "ids" else v) for k, v in exam.items()}


def _shot_key(t: dict) -> str:
    return t["kind"]


SHOT_CHARS = 4500


def few_shots(tasks: list[dict]) -> dict[str, list[dict]]:
    """Two short TRAIN tasks per kind, fixed by hash; for logic-need one relevant (NEED) and one control, so the
    shots do not teach "always NEED" or "never NEED"."""
    pool = sorted((t for t in tasks if t["split"] == "train" and len(t["prompt"]) <= SHOT_CHARS),
                  key=lambda t: hashlib.sha256(f"shot:{t['id']}".encode()).hexdigest())
    out = {}
    for kind in {t["kind"] for t in pool}:
        same_kind = [t for t in pool if t["kind"] == kind]
        if kind == "logic-need":
            rel = [t for t in same_kind if t["completion"].startswith("NEED:")][:1]
            ctl = [t for t in same_kind if not t["completion"].startswith("NEED:")][:1]
            out[kind] = rel + ctl
        elif kind == "logic-predict":
            same = [t for t in same_kind if t["completion"] == "SAME"][:1]
            differ = [t for t in same_kind if t["completion"] != "SAME"][:1]
            out[kind] = same + differ
        else:
            out[kind] = same_kind[:2]
    return out


RETRY = ("Compiler feedback on that attempt:\n{feedback}\n\nGive a corrected edit script for the function as "
         "originally shown (same line numbers), in the same format.")       # identical to eval/train_grpo.py


class Feedback:
    """The grader (reward_server.py, exam/check splits) as a feedback service for multi-turn explain answers: the same
    compiler evidence the solver's repair loop gets. Grades still come from logic_grade over the FINAL answers."""

    def __init__(self, tasks_path: Path, context: Path, v2: bool = False):
        import subprocess
        import threading
        here = Path(__file__).resolve().parent / "results/edit-capability-20261002"
        self.proc = subprocess.Popen([sys.executable, "reward_server.py", str(tasks_path), "--context", str(context),
                                      "--splits", "exam,check", *(["--feedback-v2"] if v2 else [])], cwd=here,
                                     stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True)
        if not json.loads(self.proc.stdout.readline()).get("ready"):
            raise RuntimeError("feedback service did not start")
        self.lock = threading.Lock()

    def __call__(self, task_id: str, answer: str) -> dict:
        with self.lock:
            self.proc.stdin.write(json.dumps({"id": task_id, "answer": answer, "feedback": True}) + "\n")
            self.proc.stdin.flush()
            out = json.loads(self.proc.stdout.readline())
        if "error" in out:
            raise RuntimeError(out["error"])
        return out


def run(exam_path: Path, arm: str, out: Path, url: str, jobs: int, turns: int = 1, context: Path | None = None,
        keep_best: bool = False, first_samples: int = 1, feedback_v2: bool = False) -> dict:
    """keep_best: the final answer is the attempt closest to the target (not the last), and a retry that got worse is
    pointed back to the best one. first_samples: k first-attempt draws for explain tasks, the compiler picks the
    closest. feedback_v2: verbatim compiler errors. All three default off, so earlier runs stay reproducible. For
    arm-vs-arm comparisons use --jobs 1: batched serving is not bit-deterministic even at temperature 0."""
    from tools.lora_serve.client import InferenceClient
    exam = json.loads(exam_path.read_text())
    data = Path(exam["tasks"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != exam["tasks_sha256"]:
        raise SystemExit("the tasks file changed since the exam was frozen")
    ids = set(exam["ids"])
    every = [json.loads(line) for line in data.decode("utf-8").splitlines()]
    tasks = [t for t in every if t["id"] in ids]
    prior = [json.loads(line) for line in out.read_text().splitlines()] if out.exists() else []
    if any(r["arm"] != arm for r in prior) or len({r["id"] for r in prior}) != len(prior):
        raise ValueError("answer log has a different arm or duplicate ids")
    done = {r["id"] for r in prior}
    todo = [t for t in tasks if t["id"] not in done]
    client = InferenceClient(url, timeout=900.0)
    dec = exam["decoding"]
    # `<model>_fs`: the same model shown worked TRAIN examples of the task's kind first, so a format the untrained
    # model was never taught is separated from compiler knowledge it lacks (LOGIC_PILOT.md).
    model = arm[:-3] if arm.endswith("_fs") else arm
    shots = few_shots(every) if arm.endswith("_fs") else {}
    needs_feedback = turns > 1 or first_samples > 1
    feedback = Feedback(Path(exam["tasks"]), context, v2=feedback_v2) if needs_feedback else None

    def ask(t):
        from eval.repair_prompts import logic_retry_prompt
        messages = []
        for s in shots.get(_shot_key(t), []):
            messages += [{"role": "user", "content": s["prompt"]}, {"role": "assistant", "content": s["completion"]}]
        messages.append({"role": "user", "content": t["prompt"]})
        attempts, distances = [], []
        explain = t["kind"] == "logic-explain"
        best = None                                   # (distance, attempt index, text, feedback)
        try:
            for turn in range(turns if explain else 1):
                k = first_samples if (turn == 0 and explain) else 1
                drawn = []
                for i in range(k):
                    # Draw 0 is the frozen greedy decoding; further first-attempt draws sample, and the compiler picks.
                    r = client.chat(messages, model=None if model == "base" else model,
                                    temperature=dec["temperature"] if i == 0 else 0.7, seed=dec["seed"] + i,
                                    max_tokens=dec["max_tokens"])
                    v = feedback(t["id"], r.text) if (feedback is not None and explain) else None
                    drawn.append((r.text, v))
                # Non-losing exploration (evolvability-trial-20260928): an exact extra draw is taken; otherwise the
                # greedy draw 0 (the "production" path) continues. Row-count alone never promotes an extra draw.
                exact = [d for d in drawn if (d[1] or {}).get("full")]
                text, verdict = exact[0] if exact else drawn[0]
                attempts.append(text)
                distances.append((verdict or {}).get("distance"))
                if verdict is not None and (best is None or verdict["distance"] < best[0]):
                    best = (verdict["distance"], len(attempts), text, verdict["feedback"])
                if verdict is None or verdict["full"] or turn + 1 >= turns:
                    break
                worse = (keep_best and best is not None and best[1] != len(attempts)
                         and verdict["distance"] > best[0])
                messages += [{"role": "assistant", "content": text},
                             {"role": "user", "content": logic_retry_prompt(
                                 verdict["feedback"], worse_than=(best[1], best[2], best[3]) if worse else None)}]
        except Exception as exc:
            return {"id": t["id"], "arm": arm, "error": repr(exc)}
        final = best[2] if (keep_best and best is not None) else attempts[-1]
        return {"id": t["id"], "arm": arm, "answer": final, "turns": len(attempts), "attempts": attempts,
                "distances": distances, "keep_best": keep_best, "first_samples": first_samples,
                "receipt": r.receipt,
                "prompt_tokens": r.receipt.get("prompt_tokens"), "completion_tokens": r.receipt.get("completion_tokens"),
                "wall_ms": r.receipt.get("wall_ms")}

    n = 0
    with concurrent.futures.ThreadPoolExecutor(jobs) as ex, open(out, "a") as f:
        for future in concurrent.futures.as_completed([ex.submit(ask, t) for t in todo]):
            row = future.result()
            f.write(json.dumps(row) + "\n")
            f.flush()
            n += 1
            if n % 50 == 0:
                print(f"{arm}: {n}/{len(todo)}", flush=True)
    results = [json.loads(line) for line in out.read_text().splitlines()]
    answered = {r["id"] for r in results if "error" not in r}
    errors = sum("error" in r for r in results)
    return {"arm": arm, "frozen": len(ids), "answered": len(answered & ids), "errors": errors,
            "complete": ids <= answered and not errors}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("tasks", type=Path)
    f.add_argument("--out", type=Path, required=True)
    f.add_argument("--splits", default="exam,check")
    f.add_argument("--check-sample", type=int, default=600)
    f.add_argument("--max-prompt-chars", type=int, default=9000)
    f.add_argument("--kinds", default="", help="comma-separated task kinds to keep (default: all)")
    f.add_argument("--max-tokens", type=int, default=512, help="answer length cap, frozen with the exam")
    f.add_argument("--train-sample", type=int, default=0, help="practice sets: sample this many train tasks")
    f.add_argument("--exclude", type=Path, nargs="*", default=[], help="task files whose ids are left out")
    r = sub.add_parser("run")
    r.add_argument("exam", type=Path)
    r.add_argument("--arm", required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--url", default="http://127.0.0.1:8101")
    r.add_argument("--jobs", type=int, default=6)
    r.add_argument("--turns", type=int, default=1, help="attempts per explain task, with compiler feedback")
    r.add_argument("--context", type=Path, help="checked context rows (required for --turns > 1)")
    r.add_argument("--keep-best", action="store_true", help="final answer = closest attempt; worse retries redirected")
    r.add_argument("--first-samples", type=int, default=1, help="first-attempt draws for explain; compiler picks")
    r.add_argument("--feedback-v2", action="store_true", help="verbatim compiler errors in feedback")
    a = ap.parse_args(argv)
    if a.cmd == "freeze":
        print(json.dumps(freeze(a.tasks, a.out, a.splits.split(","), a.check_sample, a.max_prompt_chars,
                                         [k for k in a.kinds.split(",") if k] or None, a.max_tokens,
                                         a.train_sample,
                                         {json.loads(line)["id"] for p in a.exclude for line in open(p)
                                          if line.strip()}), indent=1))
    else:
        if (a.turns > 1 or a.first_samples > 1) and not a.context:
            ap.error("--turns > 1 / --first-samples > 1 need --context for compiler feedback")
        result = run(a.exam, a.arm, a.out, a.url, a.jobs, a.turns, a.context, a.keep_best, a.first_samples,
                     a.feedback_v2)
        print(json.dumps(result))
        return 0 if result["complete"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
