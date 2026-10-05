"""Bounded two-function normal-controller canary in private WSL state.

Normal completion_campaign executes intake; fast_campaign then resumes the
completed-intake checkpoint in deterministic-only mode to check idempotence.
The fast controller deliberately does not dispatch pending intake nodes.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

from eval import completion_campaign, fast_campaign  # noqa: E402
from eval.campaign_workers import isolate  # noqa: E402
from run_clean_canary import CANARIES, _attempts, _snapshot  # noqa: E402


def _state_summary(state: dict) -> dict:
    return {"status": state["status"], "nodes": {
        name: {"status": node["status"], "attempt_id": node.get("attempt_id"),
               "source_sha256": node.get("source_sha256"),
               "score": node.get("score"), "jobs": [
                   {"profile": job.get("profile"), "status": job.get("status"),
                    "evidence_key": job.get("evidence_key")}
                   for job in node.get("jobs", [])]}
        for name, node in state["nodes"].items()},
        "inflight": bool(state.get("inflight")),
        "fast_inflight": len(state.get("fast_inflight", [])),
        "fast_last_session": (state.get("fast_metrics") or {}).get("last_session")}


def _compact_attempt(attempt: dict) -> dict:
    contexts = (attempt.get("provenance_hashes") or {}).get("binary_type_context") or []
    context = contexts[0] if contexts else {}
    return {key: attempt.get(key) for key in (
        "attempt_id", "parent_attempt_id", "strategy", "source_sha256",
        "compiled", "score", "exact", "model")} | {
        "frontend_passed": (attempt.get("frontend") or {}).get("passed"),
        "certificate_exact": (attempt.get("verification") or {}).get("exact"),
        "evidence_hashes": context.get("evidence", {}),
        "preprocessed_sha256": context.get("preprocessing", {}).get("preprocessed_sha256")}


def run(source_repo: Path, source_db: Path, out: Path, copy_to: Path | None) -> dict:
    if out.exists():
        raise FileExistsError(f"refusing to overwrite controller canary: {out}")
    out.mkdir(parents=True)
    snapshot = _snapshot(source_db, out / "private.sqlite")
    baseline = snapshot.pop("initial_exact_addrs")
    repo = out / "repo"
    for function in CANARIES:
        isolate(source_repo, repo, function)
    state_path = out / "state.json"
    config = dict(repo=repo, db=out / "private.sqlite", project=ROOT,
                  state_path=state_path, functions=CANARIES,
                  max_work_items=2, model_calls=0, scheduler="evidence-v1",
                  endpoint="http://127.0.0.1:1")
    with (out / "normal-controller.log").open("w") as log, redirect_stdout(log):
        initial = completion_campaign.run(**config)
    if set(initial["nodes"]) != set(CANARIES):
        raise AssertionError("controller cohort differs from canaries")
    args = SimpleNamespace(
        repo=repo, db=out / "private.sqlite", project=ROOT, state=state_path,
        worker_root=out / "workers", resume=True, deterministic_only=True,
        scheduler="evidence-v1", workers=2, dispatch="wave", model_parallel=1,
        model_workers=1, tasks_per_worker=1, reasoned_effort="profile",
        max_work_items=2, model_calls=0, model="gpt-oss:20b",
        endpoint="http://127.0.0.1:1", timeout=1200, num_predict=6000,
        integrate=False, runtime_plan=None)
    with (out / "fast-resume.log").open("w") as log, redirect_stdout(log):
        resumed = fast_campaign.run(args)
    with sqlite3.connect(out / "private.sqlite") as conn:
        all_ids = [r[0] for r in conn.execute("SELECT id FROM attempts ORDER BY id")]
        private_exact = {r[0] for r in conn.execute(
            "SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    with sqlite3.connect(f"file:{source_db}?mode=ro", uri=True) as upstream:
        source_exact = {r[0] for r in upstream.execute(
            "SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    attempts = {name: _attempts(out / "private.sqlite", name, 0) for name in CANARIES}
    result = {"kind": "clean-binary-type-normal-controller-canary",
              "selected_route": "source-independent binary types",
              "intake_alternatives_also_logged": True, "db": snapshot,
              "normal": _state_summary(initial), "resumed": _state_summary(resumed),
              "attempts": attempts,
              "attempt_ids": all_ids,
              "private_exact_count": len(private_exact),
              "source_exact_before": len(baseline),
              "source_exact_after": len(source_exact),
              "source_exact_lost": sorted(baseline - source_exact),
              "log_paths": {"normal": str(out / "normal-controller.log"),
                            "fast_resume": str(out / "fast-resume.log")}}
    selected = {name: next((a for a in attempts[name]
                            if a["attempt_id"] == result["normal"]["nodes"][name]["attempt_id"]), None)
                for name in CANARIES}
    result["selected_attempts"] = selected
    result["checks"] = {
        "two_normal_work_items": sum(len(n["jobs"]) for n in result["normal"]["nodes"].values()) == 2,
        "both_object_exact": all(n["status"] == "object_exact" for n in result["normal"]["nodes"].values()),
        "selected_clean_binary_exact": all(
            a and (a["strategy"] or "").startswith("campaign-intake:binary-types:")
            and a["parent_attempt_id"] is None and a["compiled"] and a["exact"]
            and (a["frontend"] or {}).get("passed") is True
            and (a["verification"] or {}).get("exact") is True
            for a in selected.values()),
        "all_attempts_model_free": all(not a["model"] for group in attempts.values() for a in group),
        "all_attempts_logged": len(all_ids) == sum(len(group) for group in attempts.values()),
        "no_inflight": not result["resumed"]["inflight"] and not result["resumed"]["fast_inflight"],
        "repeat_dispatch_zero": result["resumed"]["fast_last_session"].get("completed_items") == 0,
        "source_exact_preserved": not result["source_exact_lost"],
    }
    # The private DB owns full compiler and context receipts. The copied
    # review artifact stays small and never contains candidate source bodies.
    result["attempts"] = {name: [_compact_attempt(a) for a in group]
                          for name, group in attempts.items()}
    result["selected_attempts"] = {name: _compact_attempt(a) if a else None
                                   for name, a in selected.items()}
    compact = out / "controller-canary.json"
    compact.write_text(json.dumps(result, indent=2))
    if copy_to is not None:
        copy_to.mkdir(parents=True, exist_ok=True)
        (copy_to / "controller-canary.json").write_text(compact.read_text())
        for name in ("normal-controller.log", "fast-resume.log"):
            (copy_to / name).write_text((out / name).read_text())
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.home() / "decomp/sbk1")
    parser.add_argument("--db", type=Path, default=Path.home() / "decomp/kb-sbk1.sqlite")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--copy-to", type=Path)
    args = parser.parse_args()
    result = run(args.repo.resolve(), args.db.resolve(), args.out.resolve(),
                 args.copy_to.resolve() if args.copy_to else None)
    print(json.dumps({"checks": result["checks"], "attempt_ids": result["attempt_ids"],
                      "normal_status": result["normal"]["status"],
                      "resumed_status": result["resumed"]["status"]}, indent=2))
    return 0 if all(result["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
