"""Paired A/B: local gpt-oss:20b against the hosted Codex CLI, same kernel.

Reads the preregistered cohort below, runs both arms on identical budgets from
identical parents, and writes one summary JSON. Sequential on purpose: the two
arms share nothing except the machine, and overlapping them would put GPU
contention inside the measurement.

    python3 eval/results/codex-ab-20260911/run_ab.py --arms treatment
    python3 eval/results/codex-ab-20260911/run_ab.py [--functions NAME ...]

**One resident model, or the machine dies.** gpt-oss:20b at this context is
~11.9 GB and the card has 16 GB, so a second copy does not fail -- the driver
spills it through system RAM and the whole desktop freezes while both copies
thrash. This happened: the control arm defaulted to the 11434 endpoint while
another experiment already had the model resident on 11435, leaving 2.3 GB of
31 GB free. `CAMPAIGN_PERFORMANCE.md` warns about exactly this, and a warning in
a document did not stop it, so the preflight below enforces it instead.

The treatment arm is hosted and costs nothing locally, which is what makes
coexistence possible: it can run at full speed beside somebody else's GPU work.
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
sys.path.insert(0, str(ROOT))
from solver import llm                                        # noqa: E402
RESULTS = Path(__file__).resolve().parent
DB = Path.home() / "decomp/kb-sbk1-codexab-20260911.sqlite"
REPO = Path.home() / "decomp/sbk1"

# Preregistered in PREREGISTRATION.md. Do not edit to chase a result.
COHORT = [
    ("initControllerPakRaceRecordSaveFlow", 23545),
    ("updateCourseSelectCourseDescription", 16006),
    ("updateRacePlayerMode37AerialTrick", 23283),
    ("serviceRumbleMotorRequest", 28110),
    ("alLoadParam", 29675),
    ("packFixedTransformMatrix", 24142),
]

ARMS = {
    "control-gpt-oss-20b": ["--provider", "solver.modelrepair:OllamaProvider",
                            "--model", "gpt-oss:20b"],
    "treatment-codex-spark": ["--provider", "solver.codexprovider:provider",
                              "--model", "gpt-5.3-codex-spark"],
}
LOCAL_ARMS = {"control-gpt-oss-20b"}      # arms that touch the GPU at all

BUDGET = ["--draws", "1", "--depth", "4", "--beam", "2", "--max-calls", "8",
          "--seed", "20260911", "--timeout", "600"]

# Idle milliseconds after every local call. The GPU sits at ~99% during
# generation, which is what makes the desktop stutter; this hands the
# compositor a regular window. See solver/llm.py::_throttle.
LOCAL_GAP_MS = os.environ.get("SOLVER_GAP_MS", "250")


def ollama_endpoints() -> list[str]:
    """Both known servers, addressed the way WSL reaches Windows."""
    base = llm.host().rsplit(":", 1)[0]
    return [f"{base}:11434", f"{base}:11435"]


def resident_models() -> dict[str, list[dict]]:
    """What each local server currently holds, by endpoint."""
    found = {}
    for endpoint in ollama_endpoints():
        try:
            with urllib.request.urlopen(f"{endpoint}/api/ps", timeout=15) as r:
                models = json.load(r).get("models", [])
        except Exception:                        # a server that is not up is fine
            continue
        if models:
            found[endpoint] = models
    return found


def preflight_local_arm() -> str:
    """Refuse to add a second resident copy; return the endpoint to share.

    Returns the endpoint that already has the model so this run joins it rather
    than loading its own. Requests to one server queue; two servers do not.
    """
    resident = resident_models()
    loaded = {endpoint: [m for m in models if str(m.get("name", "")).startswith("gpt-oss")]
              for endpoint, models in resident.items()}
    loaded = {endpoint: models for endpoint, models in loaded.items() if models}

    if len(loaded) > 1:
        raise SystemExit(
            "REFUSING to start a local arm: gpt-oss is already resident on "
            f"{len(loaded)} servers ({', '.join(loaded)}). That is the state "
            "that freezes this machine. Unload all but one first:\n"
            "  curl -s ENDPOINT/api/generate -d '{\"model\":\"gpt-oss:20b\",\"keep_alive\":0}'")
    if loaded:
        endpoint = next(iter(loaded))
        print(f"sharing the resident model on {endpoint} "
              f"(requests queue behind any other experiment)", flush=True)
        return endpoint
    print("no model resident; the local arm will load one", flush=True)
    return ollama_endpoints()[0]


def run_one(function: str, attempt_id: int, arm: str,
            endpoint: str = "") -> dict:
    out = RESULTS / f"{arm}-{function}.json"
    command = [sys.executable, "-m", "eval.agentrepair",
               "--repo", str(REPO), "--db", str(DB),
               "--function", function, "--attempt-id", str(attempt_id),
               "--out", str(out),
               "--best-source-out", str(RESULTS / f"{arm}-{function}.best.c"),
               "--cache-dir", str(Path.home() / f".cache/game-decomp/codexab-{arm}"),
               *ARMS[arm], *BUDGET]
    environment = dict(os.environ)
    if arm in LOCAL_ARMS:
        command += ["--endpoint", endpoint]
        environment["SOLVER_GAP_MS"] = LOCAL_GAP_MS
    started = time.time()
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                               env=environment)
    elapsed = time.time() - started

    record = {"function": function, "arm": arm, "attempt_id": attempt_id,
              "wall_seconds": round(elapsed, 1), "returncode": completed.returncode,
              "receipt": str(out)}
    combined = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    if "QuotaExhausted" in combined or "quota_exhausted" in combined:
        record.update({"voided": "quota-exhausted", "error": combined[-400:]})
        return record
    if completed.returncode != 0:
        record["error"] = (completed.stderr or "")[-800:]
        return record
    receipt = json.loads(out.read_text(encoding="utf-8"))
    result = receipt.get("result", receipt)
    # A run where EVERY call came back empty is not a result. On the hosted arm
    # that is what a spent quota looks like from inside the kernel, and four
    # such runs were nearly recorded as the model failing.
    calls = result.get("calls_attempted") or 0
    if calls and result.get("incomplete_responses") == calls:
        record.update({"voided": "every-call-empty",
                       "calls_attempted": calls,
                       "note": "no usable generation; not counted as a trial"})
        return record
    record.update({
        "exact": bool(result.get("exact")),
        "parent_score": (receipt.get("root") or {}).get("score"),
        "best_score": (result.get("champions", {}).get("byte") or {}).get("score"),
        "calls_attempted": result.get("calls_attempted"),
        "generations": result.get("generations"),
        "compiling_children": result.get("compiling_children"),
        "invalid_proposals": result.get("invalid_proposals"),
        "incomplete_responses": result.get("incomplete_responses"),
        "log": result.get("log", [])[-6:],
    })
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--functions", nargs="*", default=None)
    parser.add_argument("--arms", nargs="*", default=list(ARMS),
                        choices=list(ARMS) + ["control", "treatment"],
                        help="run only these arms; the hosted treatment arm "
                             "uses no local GPU and can run beside other work")
    args = parser.parse_args()
    cohort = [(f, a) for f, a in COHORT
              if args.functions is None or f in args.functions]
    arms = [arm for arm in ARMS
            if arm in args.arms or arm.split("-")[0] in args.arms]

    endpoint = preflight_local_arm() if set(arms) & LOCAL_ARMS else ""

    summary_path = RESULTS / "summary.json"
    records: list[dict] = []
    if summary_path.exists():                    # resume without redoing work
        records = json.loads(summary_path.read_text(encoding="utf-8")).get("runs", [])
    # A voided run is not done: it must be retried, not skipped as complete.
    records = [r for r in records if not r.get("voided")]
    done = {(r["function"], r["arm"]) for r in records
            if r.get("returncode") == 0 and not r.get("voided")}

    for function, attempt_id in cohort:
        for arm in arms:
            if (function, arm) in done:
                print(f"skip {arm} {function} (already done)", flush=True)
                continue
            print(f"=== {arm} {function} ===", flush=True)
            record = run_one(function, attempt_id, arm, endpoint)
            records.append(record)
            if record.get("voided") == "quota-exhausted":
                print("STOPPING: model quota exhausted. The remaining cohort "
                      "has NOT been tested; rerun after the quota resets and "
                      "this run will resume where it stopped.", flush=True)
                summary_path.write_text(json.dumps(
                    {"schema_version": 1, "kind": "codex-vs-local-ab",
                     "preregistration": "PREREGISTRATION.md",
                     "primary_criterion": "object verifier exact=true",
                     "runs": records}, indent=2) + "\n", encoding="utf-8")
                raise SystemExit(2)
            print(json.dumps({k: v for k, v in record.items() if k != "log"}),
                  flush=True)
            summary_path.write_text(json.dumps(
                {"schema_version": 1,
                 "kind": "codex-vs-local-ab",
                 "preregistration": "PREREGISTRATION.md",
                 "primary_criterion": "object verifier exact=true",
                 "runs": records}, indent=2) + "\n", encoding="utf-8")

    exacts = {arm: sum(1 for r in records if r["arm"] == arm and r.get("exact"))
              for arm in arms}
    print("\nEXACT MATCHES:", json.dumps(exacts))
    print("summary:", summary_path)


if __name__ == "__main__":
    main()
