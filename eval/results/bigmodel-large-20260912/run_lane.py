"""Large-function lane: a bigger LOCAL model on the functions closest to done.

    python3 eval/results/bigmodel-large-20260912/run_lane.py --hours 6

One GPU, one model. A second resident model spills through system RAM and
locks the machine (2026-09-11), so this lane never shares the card with the
campaign. It pauses the campaign at a work-item boundary, unloads the
campaign's model, runs the queue top-down, then unloads its own model and
resumes the campaign -- in a `finally`, so an error or Ctrl-C cannot strand the
campaign paused or leave a 19 GB model resident.

The queue (queue.json) is the campaign's own unmatched, compiling functions
over 80 instructions, ordered by score, each pinned to the exact candidate the
campaign holds (matched by SHA256). Functions whose residual has no
source-shape faults -- zero faults, or relocations only -- are skipped with a
reason: a model cannot edit its way out of a difference the diff does not show,
and an hour of 32B inference there is waste, not a trial.

A match here is evidence about MODEL STRENGTH only against the campaign's
history on the same functions, where gpt-oss:20b already made hundreds of calls.
Budgets differ from the campaign's, so it is a pilot, not a controlled result,
and any match needs the contamination check before it counts as capability.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from solver import llm                                            # noqa: E402

RUN = ROOT / "eval/results/resume-pipeline-20260908"
SERVICE_PY = "/home/grant/decomp/sbk1/.venv/bin/python"
REPO = Path.home() / "decomp/sbk1"
DB = Path.home() / "decomp/kb-sbk1-bigmodel-20260912.sqlite"
CAMPAIGN_MODEL = "gpt-oss:20b"
BASE = llm.host().rsplit(":", 1)[0]
ENDPOINT = f"{BASE}:11435"          # the campaign's server: one scheduler, one card
OTHER = f"{BASE}:11434"

# Set from the command line in main(); one lane run = one (model, context, queue).
MODEL = "qwen3:32b"
CONTEXT = 0                          # 0 = the campaign's historical 32K ceiling
OUT = HERE
BUDGET: list[str] = []


def budget(num_predict: int, timeout: int, campaign_shape: bool) -> list[str]:
    """Kernel settings. `campaign_shape` mirrors completion_campaign._run_profile.

    The 32K refusals were not produced by plain repair prompts: the campaign runs
    the kernel resilient (semantic counterexample panels) with header context,
    and THOSE are what outgrow the ceiling -- which is why 45-instruction
    functions appear among the refused. A context test that sends the plain
    prompt would measure nothing, because that prompt already fits.
    """
    args = ["--draws", "1", "--depth", "4", "--max-calls", "8",
            "--seed", "20260912", "--timeout", str(timeout),
            "--num-predict", str(num_predict), "--structured-output"]
    if campaign_shape:
        return args + ["--beam", "3", "--resilient", "--include-header-context",
                       "--retry-invalid", "--think", "low", "--temperature", "0.35"]
    return args + ["--beam", "2"]


def log(message: str) -> None:
    print(f"[lane {time.strftime('%H:%M:%S')}] {message}", flush=True)


def service(action: str) -> dict:
    out = subprocess.run([SERVICE_PY, "-m", "eval.campaign_service", "--run",
                          str(RUN), action], cwd=ROOT, capture_output=True,
                         text=True, timeout=600)
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except Exception:
        return {"raw": (out.stdout + out.stderr)[-400:]}


def resident(endpoint: str) -> list[str]:
    try:
        with urllib.request.urlopen(f"{endpoint}/api/ps", timeout=20) as r:
            return [m["name"] for m in json.load(r).get("models", [])]
    except Exception:
        return []


def unload(endpoint: str, model: str) -> None:
    body = json.dumps({"model": model, "keep_alive": 0}).encode()
    req = urllib.request.Request(f"{endpoint}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=120).read()
    except Exception as exc:
        log(f"unload {model} on {endpoint}: {type(exc).__name__}")


def wait_drained(limit_s: int = 3600) -> None:
    """Pause takes effect at a work-item boundary; wait for the worker to exit."""
    started = time.time()
    while time.time() - started < limit_s:
        state = json.loads((RUN / "service.json").read_text(encoding="utf-8"))
        if state.get("status") == "paused" and not state.get("worker_pid"):
            log("campaign drained and paused")
            return
        time.sleep(30)
    raise SystemExit("campaign did not drain within the limit; lane not started")


def skip_reason(row: dict) -> str:
    faults = row.get("faults") or {}
    if row.get("fault_total", 0) == 0 and not row.get("instruction_delta"):
        return "no source-shape faults in the diff (object difference lies elsewhere)"
    if faults and set(faults) <= {"relocation"}:
        return "relocation-only residual: symbol/data layout, not function body shape"
    return ""


STOP = HERE / "STOP"


def stop_requested() -> bool:
    """The Windows-side watchdog writes STOP when free RAM gets critical.

    WSL cannot see Windows memory, and an offloaded 32B model is exactly the
    kind of load that pushes this machine into the freeze. So the brake lives
    outside the process that causes the pressure, and the lane polls it.
    """
    return STOP.exists()


def run_function(row: dict) -> dict:
    name = row["function"]
    out = OUT / f"{name}.json"
    # The campaign's venv: resilient mode's semantic panels need its dependencies.
    command = [SERVICE_PY, "-m", "eval.agentrepair",
               "--repo", str(REPO), "--db", str(DB), "--function", name,
               "--source", str(ROOT / row["source_path"]),
               "--out", str(out), "--best-source-out", str(OUT / f"{name}.best.c"),
               "--cache-dir", str(Path.home() / f".cache/game-decomp/lane-{OUT.name}"),
               "--provider", "solver.modelrepair:OllamaProvider",
               "--model", MODEL, "--endpoint", ENDPOINT, *BUDGET]
    env = dict(os.environ, SOLVER_GAP_MS="250")
    if CONTEXT:
        env["SOLVER_FIXED_CONTEXT"] = str(CONTEXT)
    else:
        env.pop("SOLVER_FIXED_CONTEXT", None)
    started = time.time()
    log_path = OUT / f"{name}.log"
    with log_path.open("w", encoding="utf-8") as sink:
        process = subprocess.Popen(command, cwd=ROOT, stdout=sink,
                                   stderr=subprocess.STDOUT, text=True, env=env)
        while process.poll() is None:
            if stop_requested():
                process.terminate()
                try:
                    process.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise SystemExit(f"STOP requested during {name} (memory brake)")
            time.sleep(5)
    record = {"function": name, "instructions": row["instructions"],
              "campaign_score": row["score"], "campaign_faults": row.get("faults"),
              "wall_seconds": round(time.time() - started, 1),
              "returncode": process.returncode, "receipt": out.name}
    if process.returncode != 0:
        record["error"] = log_path.read_text(encoding="utf-8", errors="replace")[-800:]
        return record
    result = json.loads(out.read_text(encoding="utf-8")).get("result", {})
    calls = result.get("calls_attempted") or 0
    record.update({
        "exact": bool(result.get("exact")),
        "best_score": (result.get("champions", {}).get("byte") or {}).get("score"),
        "calls_attempted": calls, "compiling_children": result.get("compiling_children"),
        "invalid_proposals": result.get("invalid_proposals"),
        "incomplete_responses": result.get("incomplete_responses"),
        "log": (result.get("log") or [])[-6:],
    })
    if calls and result.get("incomplete_responses") == calls:
        record["voided"] = "every call empty; not a trial"
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=6.0)
    parser.add_argument("--limit", type=int, default=0, help="max functions tried")
    parser.add_argument("--model", default="qwen3:32b")
    parser.add_argument("--queue", default="queue.json")
    parser.add_argument("--tag", default="", help="output subdirectory for this run")
    parser.add_argument("--context", type=int, default=0,
                        help="fixed context allocation (e.g. 65536); 0 = 32K ceiling")
    parser.add_argument("--num-predict", type=int, default=8000)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--campaign-shape", action="store_true",
                        help="resilient + header context, as the campaign worker runs")
    args = parser.parse_args()
    deadline = time.time() + args.hours * 3600

    global MODEL, CONTEXT, OUT, BUDGET
    MODEL, CONTEXT = args.model, args.context
    OUT = HERE / args.tag if args.tag else HERE
    OUT.mkdir(parents=True, exist_ok=True)
    BUDGET = budget(args.num_predict, args.timeout, args.campaign_shape)

    queue = json.loads((HERE / args.queue).read_text(encoding="utf-8"))["queue"]
    summary_path = OUT / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) \
        if summary_path.exists() else {"model": MODEL, "context": CONTEXT or 32768,
                                       "queue": args.queue, "runs": []}
    done = {r["function"] for r in summary["runs"]
            if not r.get("voided") and r.get("returncode", 0) == 0}

    if resident(OTHER):
        raise SystemExit(f"refusing: {OTHER} holds {resident(OTHER)}; unload it first")
    if stop_requested():
        raise SystemExit(f"refusing: {STOP} exists (delete it once memory is freed)")

    log("pausing campaign")
    service("pause")
    wait_drained()
    try:
        unload(ENDPOINT, CAMPAIGN_MODEL)
        time.sleep(5)
        if resident(ENDPOINT) or resident(OTHER):
            raise SystemExit("a model is still resident: "
                             f"{resident(ENDPOINT)} {resident(OTHER)}")
        tried = 0
        for row in queue:
            if row["function"] in done:
                continue
            if stop_requested():
                log("STOP file present; ending lane")
                break
            if time.time() > deadline or (args.limit and tried >= args.limit):
                log("budget reached")
                break
            reason = skip_reason(row)
            if reason:
                summary["runs"].append({"function": row["function"], "skipped": reason,
                                        "campaign_score": row["score"], "returncode": 0})
                log(f"skip {row['function']}: {reason}")
            else:
                log(f"run {row['function']} ({row['instructions']} insns, "
                    f"score {row['score']})")
                record = run_function(row)
                summary["runs"].append(record)
                tried += 1
                log(json.dumps({k: v for k, v in record.items() if k != "log"}))
                models = resident(ENDPOINT) + resident(OTHER)
                if len(set(models)) > 1:
                    raise SystemExit(f"duplicate residency detected {models}; stopping")
            summary_path.write_text(json.dumps(summary, indent=2) + "\n",
                                    encoding="utf-8")
    finally:
        log("unloading lane model and resuming campaign")
        unload(ENDPOINT, MODEL)
        time.sleep(5)
        resumed = service("resume")
        log(f"campaign resume: status={resumed.get('status')}")
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        exact = [r["function"] for r in summary["runs"] if r.get("exact")]
        log(f"EXACT: {len(exact)} {exact}")


if __name__ == "__main__":
    main()
