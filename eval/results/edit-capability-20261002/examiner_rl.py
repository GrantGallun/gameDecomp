"""Examiner RL, sequentially on one GPU: the examiner is rewarded for exams the solver can sometimes but not always pass.

    python3 examiner_rl.py --out DIR --solver SOLVER_ADAPTER --weak WEAK.json [--cycles 20] [--prompts 16] [--group 4]

One server (tools/lora_serve, --continuous --admin --sleep-mode) holds the base model with two LoRAs, `solver` and
the current `examiner_<c>`. Per cycle:
  1. the examiner writes GROUP edits for each of PROMPTS (weak class x anonymized TRAIN function) prompts;
  2. each edit is applied and COMPILED (tools/anonymize + public_plant): no edit / no compile = format penalty,
     an edit the compiler erases = 0;
  3. the solver attempts each real task K times; examiner reward = self_curriculum.examiner_reward (1 - solve rate
     when 0 < solves < K, else 0): hard but fair pays, impossible and trivial do not;
  4. POST /admin/sleep (level 2 frees the GPU), eval/pg_update.py takes one on-policy GRPO step on the examiner,
     POST /admin/wake, POST /admin/adapter serves the result as `examiner_<c+1>`. No restart.
Learnable tasks (0 < solves < K) are appended to DIR/solver_tasks.jsonl for the solver's next training round.
Everything is logged per cycle (DIR/cycles.jsonl); a failure writes DIR/FAILED and stops. Scores of either model
come only from the frozen exams, never from this loop's rewards.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import self_curriculum as sc
from eval import logic_tasks as lt
from eval import repair_prompts

ROOT = Path(__file__).resolve().parents[3]
URL = "http://127.0.0.1:8101"
TPY = Path.home() / "decomp/train-venv/bin/python"
BASE = Path.home() / "decomp/models/qwen2.5-coder-7b"
PROBE = "Write a C function that returns the sum of two ints."
FORMAT_PENALTY = -0.5        # no parseable edit, or an edit that does not compile (Absolute Zero's format penalty)
CONTEXT = Path.home() / "decomp/experiments/edit-capability-20261002/public/context-v3.jsonl"


def post(path: str, body: dict | None = None, timeout: float = 900.0) -> dict:
    req = urllib.request.Request(URL + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def start_server(out: Path, solver: Path, examiner: Path) -> subprocess.Popen:
    env = os.environ | {"SOLVER_VLLM_GPU_UTILIZATION": "0.70", "SOLVER_VLLM_MAX_SEQS": "16",
                        "SOLVER_VLLM_CONTINUOUS": "1", "SOLVER_VLLM_EAGER": "0", "SOLVER_VLLM_LORA_RANK": "8",
                        "SOLVER_VLLM_BATCHED_TOKENS": "4096", "SOLVER_VLLM_KV_GIB": "2.5", "SOLVER_VLLM_ADMIN": "1",
                        "SOLVER_VLLM_SLEEP": "1", "SOLVER_VLLM_MAX_LORAS": "2"}
    proc = subprocess.Popen(["bash", str(ROOT / ".cache/recon/serve_lowfootprint.sh"), "--adapter", f"solver={solver}",
                             "--adapter", f"examiner_0={examiner}"], cwd=ROOT, env=env,
                            stdout=open(out / "serve.log", "a"), stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(240):
        if proc.poll() is not None:
            raise RuntimeError("server exited during startup (serve.log)")
        try:
            urllib.request.urlopen(URL + "/v1/models", timeout=5).close()
            return proc
        except Exception:
            time.sleep(5)
    raise RuntimeError("server did not start")


def chat(model: str, prompt, temperature: float, seed: int, max_tokens: int) -> str:
    """`prompt` is a string (one user turn) or a message list (a tool conversation)."""
    from tools.lora_serve.client import InferenceClient
    messages = [{"role": "user", "content": prompt}] if isinstance(prompt, str) else prompt
    return InferenceClient(URL, timeout=900.0).chat(messages, model=model, temperature=temperature, seed=seed,
                                                    max_tokens=max_tokens).text


# The examiner may research before writing an exam (user, 2026-10-05). Same rules as the solver's web: read-only,
# public hosts, target game refused, logged, pages framed as untrusted data. Its exams are still edits to OUR
# anonymized functions, and the solver never sees what the examiner read, so nothing it finds can be planted where
# the solver could look it up.
# General, not decomp-specific (user, 2026-10-05): how compilers lower C and what optimisers do is the knowledge an
# examiner needs; decomp sites are one narrow corner of it.
EXAMINER_WEB_NOTE = ("\nBefore writing the edit you may research on the web how compilers lower C to machine code "
                     "and what their optimisers change, to find mistakes that are hard to see in compiled code.")
EXAMINER_MAX_CALLS = 2


def cycle(c: int, out: Path, a, profile: dict, fns: list, bmap: dict, anon_cache: dict,
          general: list | None = None) -> dict:
    import logic_grade as lg
    import public_plant as pp
    from tools import anonymize as an
    examiner = f"examiner_{c}"
    classes = [k for k in profile if k in sc.CLASS_BRIEF]
    weights = [profile[k]["weight"] for k in classes]
    plans = []
    for i in range(a.prompts):
        h = int(hashlib.sha256(f"exrl:{c}:{i}".encode()).hexdigest(), 16)
        # weighted class choice by the practice failure profile; function by hash
        x, acc = (h % 10**6) / 10**6 * sum(weights), 0.0
        cls = next((k for k, w in zip(classes, weights) if (acc := acc + w) >= x), classes[-1])
        # Game functions (IDO, the project's target) and GENERAL code under any compiler (tools/general_units.py),
        # mixed by --general-share: the corpus is all C, not only decomps.
        pool = general if general and (h >> 8) % 1000 < a.general_share * 1000 else fns
        plans.append((f"c{c}p{i}", cls, pool[(h >> 20) % len(pool)]))

    def anonymized(src):
        if src["id"] not in anon_cache:
            try:
                mapping, (ctx, orig) = an.anonymize(src["context"], [src["original_fn"]])
                name = mapping.get(src["function"], src["function"])
                obj = pp.compile_row(src, ctx + "\n" + orig, "x", bmap)
                got = pp.listing(obj, name) if obj else None
                ok = got is not None and an.masked_listing(got) == an.masked_listing(src["target"])
                anon_cache[src["id"]] = (src | {"context": ctx, "original_fn": orig, "target": got,
                                                "function": name}) if ok else None
            except Exception:
                anon_cache[src["id"]] = None
        return anon_cache[src["id"]]

    def one(job):
        gid, cls, src0, g = job
        src = anonymized(src0)
        if src is None:
            return None
        prompt = sc.PROPOSE_TEMPLATE.format(compiler=src.get("compiler") or "IDO 5.3", opt=src.get("opt") or "-O2",
                                            brief=sc.CLASS_BRIEF[cls],
                                            context=src["context"].rstrip(), numbered=lt.numbered(src["original_fn"]))
        seed = int(hashlib.sha256(f"{gid}:{g}".encode()).hexdigest(), 16) % 2**31
        if a.web:
            from tools.web_tool import TOOL_NOTE
            run = web.tool_loop(lambda msgs: chat(examiner, msgs, 1.0, seed, 400),
                                [{"role": "user", "content": prompt + EXAMINER_WEB_NOTE
                                  + TOOL_NOTE.format(n=EXAMINER_MAX_CALLS)}], EXAMINER_MAX_CALLS)
            text, context_msgs, calls = run["final"], run["messages"], run["calls"]
        else:
            text, context_msgs, calls = chat(examiner, prompt, 1.0, seed, 400), [{"role": "user", "content": prompt}], []
        # `messages` = what the final edit was conditioned on; pg_update trains the final turn on exactly that.
        sample = {"group": gid, "prompt": prompt, "messages": context_msgs, "completion": text, "class": cls,
                  "function": src["function"], "web_calls": calls, "recipe": src.get("recipe") or "game-ido53"}
        script = "\n".join(line.strip() for line in text.splitlines() if lt.EDIT.match(line.strip()))
        edited = lt.apply_script(src["original_fn"], script) if script else None
        if edited is None or edited == src["original_fn"]:
            return sample | {"reward": FORMAT_PENALTY, "outcome": "no-edit"}
        obj = pp.compile_row(src, src["context"] + "\n" + edited, "ex", bmap)
        cur = pp.listing(obj, src["function"]) if obj else None
        if cur is None:
            return sample | {"reward": FORMAT_PENALTY, "outcome": "no-compile"}
        if cur == src["target"]:
            return sample | {"reward": 0.0, "outcome": "erased"}
        row = {"id": f"exrl_{cls}:{gid}:{g}", "class": f"gen_{cls}", "label": "differ", **{k: src.get(k) for k in (
            "repository", "variant", "file", "function", "split", "split_group", "opt", "recipe", "compiler")},
            "context": src["context"],
            "original_fn": src["original_fn"], "perturbed_fn": edited, "target": src["target"], "current": cur,
            "diff": pp.mine.gnu_diff(src["target"], cur)}
        try:
            task = lt.explain_task(row) | {"id": f"logic-explain:{row['id']}", "row_id": row["id"], "split": "train",
                                           "class": row["class"], "label": "differ", "variant": "base",
                                           "provenance": "examiner-rl", "function": row["function"],
                                           "prompt_version": repair_prompts.LOGIC_PROMPT_VERSION}
        except (lt.Leak, lt.TooLong, ValueError):
            return sample | {"reward": 0.0, "outcome": "unusable-task"}
        solved = sum(bool(lg.grade(task, chat("solver", task["prompt"], 0.8, 20261005 + i, 512), {row["id"]: row},
                                   bmap)["rows"]) for i in range(a.k))
        return sample | {"reward": sc.examiner_reward(solved, a.k), "outcome": f"solved{solved}", "solved": solved,
                         "task": task if sc.learnable(solved, a.k) else None, "row": row if sc.learnable(solved, a.k)
                         else None}

    from tools.web_tool import WebTool
    web = WebTool(out / "weblog_examiner.jsonl")
    jobs = [(gid, cls, src, g) for gid, cls, src in plans for g in range(a.group)]
    with concurrent.futures.ThreadPoolExecutor(16) as ex:
        samples = [s for s in ex.map(one, jobs) if s is not None]
    cdir = out / f"cycle_{c:03d}"
    cdir.mkdir(exist_ok=True)
    (cdir / "samples.jsonl").write_text("".join(json.dumps({k: v for k, v in s.items() if k not in ("task", "row")})
                                                + "\n" for s in samples))
    with open(out / "solver_tasks.jsonl", "a") as f, open(out / "solver_rows.jsonl", "a") as fr:
        for s in samples:
            if s.get("task"):
                f.write(json.dumps(s["task"]) + "\n")
                fr.write(json.dumps(s["row"]) + "\n")
    outcomes = collections.Counter(s["outcome"] for s in samples)
    stats = {"cycle": c, "samples": len(samples), "mean_reward": round(sum(s["reward"] for s in samples) /
                                                                        max(1, len(samples)), 4),
             "learnable": sum(1 for s in samples if s.get("task")), "outcomes": dict(outcomes),
             "examiner_web_calls": sum(len(s.get("web_calls") or []) for s in samples),
             "by_recipe": {r: {"n": sum(1 for s in samples if s["recipe"] == r),
                               "mean_reward": round(sum(s["reward"] for s in samples if s["recipe"] == r)
                                                    / max(1, sum(1 for s in samples if s["recipe"] == r)), 4)}
                           for r in sorted({s["recipe"] for s in samples})}}
    # Sequential update: free the GPU, one GRPO step on the examiner, wake, serve the new examiner. No restart.
    # The wake is CHECKED: a fixed greedy probe must answer identically before sleep and after wake, or the run
    # stops (a level-2 wake that left weights or adapters stale would otherwise poison every later cycle silently).
    probe = [(m, chat(m, PROBE, 0.0, 1, 48)) for m in ("solver", examiner)]
    post("/admin/sleep", {"level": 2})
    new = out / f"examiner_{c + 1}"
    prev = out / f"examiner_{c}"
    r = subprocess.run([str(TPY), "-m", "eval.pg_update", "--base", str(BASE), "--init-adapter", str(prev),
                        "--samples", str(cdir / "samples.jsonl"), "--out", str(new), "--lr", str(a.lr)],
                       cwd=ROOT, capture_output=True, text=True,
                       env=os.environ | {"SOLVER_GPU_MEMORY_FRACTION": "0.85"})
    (cdir / "pg_update.log").write_text(r.stdout + r.stderr)
    post("/admin/wake")
    for m, before in probe:
        after = chat(m, PROBE, 0.0, 1, 48)
        if after != before:
            raise RuntimeError(f"wake check failed for {m}: {before!r} != {after!r}")
    if r.returncode != 0:
        raise RuntimeError(f"pg_update failed in cycle {c} (cycle_{c:03d}/pg_update.log)")
    post("/admin/adapter", {"name": f"examiner_{c + 1}", "path": str(new)})
    stats["update"] = json.loads((new / "pg_receipt.json").read_text())
    return stats


def main(argv=None) -> int:
    import public_plant as pp
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--solver", type=Path, required=True)
    ap.add_argument("--weak", type=Path, required=True)
    ap.add_argument("--cycles", type=int, default=20)
    ap.add_argument("--prompts", type=int, default=16)
    ap.add_argument("--group", type=int, default=4)
    ap.add_argument("--k", type=int, default=4)
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--web", choices=("on", "off"), default="on", help="examiner web research (tools/web_tool.py)")
    ap.add_argument("--general", type=Path,
                    default=Path.home() / "decomp/experiments/edit-capability-20261002/public/general-units-v1.jsonl",
                    help="general-code units (tools/general_units.py), any compiler")
    ap.add_argument("--general-share", type=float, default=0.5)
    a = ap.parse_args(argv)
    a.web = a.web == "on"
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "FAILED").unlink(missing_ok=True)
    if not (a.out / "examiner_0" / "adapter_config.json").exists():
        r = subprocess.run([str(TPY), "-m", "eval.pg_update", "--base", str(BASE), "--fresh",
                            "--out", str(a.out / "examiner_0")], cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            (a.out / "FAILED").write_text("fresh examiner: " + r.stderr[-2000:])
            return 1
    profile = json.loads(a.weak.read_text())
    fns = {}
    for line in open(CONTEXT):
        r = json.loads(line)
        if r.get("context") and r.get("original_fn") and r["split"] == "train" and r["repository"] not in sc.HOLDOUT:
            fns.setdefault((r["repository"], r["variant"], r["file"], r["function"]), r)
    fns = sorted(fns.values(), key=lambda r: r["id"])
    general = sorted((json.loads(line) for line in open(a.general)), key=lambda r: r["id"]) \
        if a.general and a.general.exists() else []
    bmap, anon_cache = pp.builds(), {}
    done = [json.loads(line)["cycle"] for line in open(a.out / "cycles.jsonl")] if (a.out / "cycles.jsonl").exists() else []
    start = (max(done) + 1) if done else 0
    proc = start_server(a.out, a.solver, a.out / "examiner_0")
    try:
        if start > 0:
            post("/admin/adapter", {"name": f"examiner_{start}", "path": str(a.out / f"examiner_{start}")})
        for c in range(start, a.cycles):
            stats = cycle(c, a.out, a, profile, fns, bmap, anon_cache, general)
            with open(a.out / "cycles.jsonl", "a") as f:
                f.write(json.dumps(stats) + "\n")
            print(json.dumps({k: v for k, v in stats.items() if k != "update"}), flush=True)
    except Exception as exc:
        (a.out / "FAILED").write_text(f"{type(exc).__name__}: {exc}\n")
        return 1
    finally:
        import signal
        os.killpg(proc.pid, signal.SIGTERM)
    (a.out / "done").write_text("done\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
