"""Triage every recorded function: does the campaign's selected C behave like the game?

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/runtime-trace-20260913/replay_all.py \\
        --recordings eval/results/runtime-trace-20260913/record-all-1 --out triage-1

For each function with recordings: compile the campaign's currently selected C in
an isolated workspace (`campaign_runtime.compile_candidate`), replay the ORIGINAL
as a control (must pass every usable recording), and replay the candidate through
the same `eval.trace_panel.Panel` the repair loop uses. When the candidate fails,
run the zero-model recorded member repair (`agentrepair._recorded_member_repairs`)
and report whether its output compiles, scores and passes. Nothing here writes
campaign state; promotion would go through the normal amendment path.
"""
import argparse
import hashlib
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from eval import agentrepair, campaign_runtime, trace_panel  # noqa: E402
from solver import fresh_compile, trace_replay  # noqa: E402

RUN = ROOT / "eval/results/resume-pipeline-20260908"
REPO = Path.home() / "decomp/sbk1"
DB = Path.home() / "decomp/kb-sbk1-parkedprobe-20260913.sqlite"


class Unavailable:
    """Base panel stand-in: triage asks only what the recordings say."""
    report = {"kind": "recorded-only-triage"}

    def __call__(self, state):
        return {"status": "unavailable", "semantic_key": [], "feedback": [], "counts": {}}


def source_for(function, detail):
    for path in sorted((RUN / "campaign-artifacts").glob(f"*-{function}.*c"), reverse=True):
        if hashlib.sha256(path.read_bytes()).hexdigest() == detail["source_sha256"]:
            return path
    return None


def triage(function, detail, recordings, folder):
    row = {"function": function, "score": detail.get("score"), "attempt_id": detail.get("attempt_id"),
           "category": detail.get("category"), "recordings": len(recordings)}
    source = source_for(function, detail)
    if source is None:
        return {**row, "outcome": "source_not_found"}
    node = {"source": str(source), "source_sha256": detail["source_sha256"], "attempt_id": detail["attempt_id"],
            "address": detail["address"], "size": detail["size"]}
    try:
        isolated, ws, state, _target, _candidate = campaign_runtime.compile_candidate(
            repo=REPO, db=DB, function=function, node=node, folder=folder)
    except ValueError as error:
        return {**row, "outcome": "does_not_compile", "reason": str(error)[:300]}
    panel = trace_panel.Panel(Unavailable(), isolated, ws, function, recordings)
    target, arities, returns, symbols, size = panel.context()
    controls = []
    for record in panel.recordings:
        try:
            controls.append(trace_replay.replay(record, target, target, entry=record["entry_address"], size=size,
                                                arities=arities, return_registers=returns,
                                                symbol_map=symbols)["status"])
        except (trace_replay.UnusableRecording, ValueError, KeyError):
            controls.append("unusable")
    result = panel(state)
    rows = result.get("recorded_call_results", [])
    counts = {k: sum(r["status"] == k for r in rows) for k in ("passed", "failed", "unusable")}
    row.update(control={k: controls.count(k) for k in set(controls)}, candidate=counts,
               distance=result["counts"].get("recorded_distance"),
               unusable_reasons=sorted({reason for r in rows if r["status"] == "unusable"
                                        for reason in r.get("reasons", [])})[:4],
               difference=next((trace_replay.sentence(r.get("divergence")) for r in rows if r["status"] == "failed"), None))
    if "passed" in controls and any(c == "failed" for c in controls):
        row["outcome"] = "control_failed"                 # replay machinery disagrees with itself
    elif counts["failed"]:
        row["outcome"] = "behaves_differently"
    elif counts["passed"]:
        row["outcome"] = "behaves_identically_on_recordings"
    else:
        row["outcome"] = "no_usable_recordings"
    if counts["failed"]:
        reports = []
        conn = __import__("sqlite3").connect(folder / "attempts.sqlite")
        try:
            repaired = agentrepair._recorded_member_repairs(
                panel, function, state, fresh_compile.Names(isolated, ws), conn, ws,
                f"recorded-triage-{time.time_ns()}", {"kind": "recorded-triage"}, reports)
        finally:
            conn.commit()
            conn.close()
        row["member_repair"] = reports
        if repaired:
            best = repaired[-1]
            after = panel(best) if best.attempt.compiled else {}
            after_rows = after.get("recorded_call_results", [])
            row["member_repair_result"] = {
                "compiled": best.attempt.compiled, "score": best.attempt.score, "exact": best.attempt.exact,
                "passed": sum(r["status"] == "passed" for r in after_rows),
                "failed": sum(r["status"] == "failed" for r in after_rows)}
            (folder / "member_repair.c").write_text(best.source)
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--recordings", type=Path, action="append", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()
    details = json.loads((HERE / "details-all-20260913.json").read_text())
    out = HERE / args.out
    out.mkdir(exist_ok=True)
    done = {json.loads(line)["function"] for line in (out / "triage.jsonl").open()} if (out / "triage.jsonl").exists() else set()
    by_function = {}
    for root in args.recordings:
        for path in sorted(root.glob("*/call-*.json")):
            by_function.setdefault(path.parent.name, []).append(json.loads(path.read_text()))
    for function in sorted(by_function):
        if function in done or function not in details or (args.only and function not in args.only):
            continue
        folder = out / function
        folder.mkdir(exist_ok=True)
        started = time.time()
        try:
            row = triage(function, details[function], by_function[function], folder)
        except Exception as error:                                        # one function never stops the batch
            row = {"function": function, "outcome": "error", "reason": f"{type(error).__name__}: {error}"[:400],
                   "traceback": traceback.format_exc()[-1500:]}
        row["seconds"] = round(time.time() - started, 1)
        with (out / "triage.jsonl").open("a") as stream:
            stream.write(json.dumps(row, default=str) + "\n")
        print(json.dumps({k: row.get(k) for k in ("function", "outcome", "candidate", "member_repair_result", "seconds")},
                         default=str), flush=True)


if __name__ == "__main__":
    main()
