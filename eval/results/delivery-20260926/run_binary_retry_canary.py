"""One frozen, deterministic binary-type retry in private campaign state.

Run from WSL with the deployed FROZEN Python environment. No live campaign
state or source database is opened for writing.
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
FROZEN = HERE.parent / "resume-pipeline-20260908" / "code"
sys.path.insert(0, str(FROZEN))

from eval import campaign_state, completion_campaign, fast_campaign  # noqa: E402
from solver import repair_queue  # noqa: E402


def copy_rows(source: sqlite3.Connection, target: sqlite3.Connection, table: str,
              where: str = "", params: tuple = ()) -> int:
    columns = [row[1] for row in source.execute(f"PRAGMA table_info({table})")]
    marks = ",".join("?" for _ in columns)
    cursor = source.execute(f"SELECT * FROM {table} {where}", params)
    count = 0
    while batch := cursor.fetchmany(1000):
        target.executemany(f"INSERT INTO {table} VALUES ({marks})", batch)
        count += len(batch)
    return count


def run(source_state: Path, out: Path, function: str) -> dict:
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    live = campaign_state.read(source_state)
    queue, _ = repair_queue.project(live, completion_campaign.PROFILES)
    item = queue["work_items"].get(function)
    if not item or not item["profile"].startswith("binary_types@"):
        raise ValueError(f"{function} is not currently eligible for binary_types retry")
    node = live["nodes"][function]
    retained = completion_campaign.retained_candidates(node)
    attempt_ids = {node["attempt_id"], *(r["attempt_id"] for r in retained)}
    source_db = Path(live["config"]["db"])
    private_db = out / "private.sqlite"
    with sqlite3.connect(f"file:{source_db}?mode=ro", uri=True) as source:
        source.execute("BEGIN")
        with sqlite3.connect(private_db) as target:
            target.executescript((FROZEN / "kb/schema.sql").read_text())
            target.execute("PRAGMA foreign_keys=OFF")
            counts = {table: copy_rows(source, target, table)
                      for table in ("extraction", "tus", "functions", "evidence")}
            ids = tuple(sorted(attempt_ids))
            clause = f"WHERE id IN ({','.join('?' for _ in ids)})"
            counts["attempts"] = copy_rows(source, target, "attempts", clause, ids)
            rows = source.execute(f"SELECT DISTINCT run_id FROM attempts {clause}", ids)
            run_ids = tuple(row[0] for row in rows if row[0] is not None)
            if run_ids:
                clause = f"WHERE id IN ({','.join('?' for _ in run_ids)})"
                counts["attempt_runs"] = copy_rows(source, target, "attempt_runs", clause, run_ids)
            else:
                counts["attempt_runs"] = 0
            target.commit()
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("private database failed quick_check")
    if counts["attempts"] != len(attempt_ids):
        raise ValueError("missing incumbent or frontier attempt")

    private = dict(live)
    private["nodes"] = {function: node}
    private["config"] = {**live["config"], "db": str(private_db)}
    private.pop("repair_queue", None)
    private.pop("binary_data_catalog", None)
    private.pop("binary_data_memory", None)
    private.pop("runtime_options", None)
    private["fast_inflight"] = []
    private["inflight"] = None
    private["fast_metrics"] = {}
    state_path = out / "state.json"
    campaign_state.Store(state_path).save(private, changed=(function,))
    (out / "state-artifacts").mkdir()
    check_queue, _ = repair_queue.project(private, completion_campaign.PROFILES)
    check_item = check_queue["work_items"].get(function)
    if check_item is None or check_item["profile"] != item["profile"]:
        raise ValueError("private profile differs from live eligible binary retry")
    args = SimpleNamespace(
        repo=Path(private["config"]["repo"]), db=private_db,
        project=Path(private["config"]["project"]), state=state_path,
        worker_root=out / "workers", resume=True, deterministic_only=True,
        scheduler="evidence-v1", workers=1, dispatch="wave", model_parallel=1,
        model_workers=1, tasks_per_worker=1, reasoned_effort="profile",
        max_work_items=1, model_calls=private["config"]["model_calls"],
        model=private["config"]["model"], endpoint=private["config"]["endpoint"],
        timeout=private["config"]["timeout"], num_predict=private["config"]["num_predict"],
        integrate=False, runtime_plan=None)
    error = None
    try:
        with (out / "fast-worker.log").open("w") as log, redirect_stdout(log):
            finished = fast_campaign.run(args)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        finished = campaign_state.read(state_path)
    resulting = finished["nodes"][function]
    jobs = resulting.get("jobs", [])
    receipt = Path(jobs[-1]["receipt"]) if len(jobs) > len(node["jobs"]) else None
    result = {
        "function": function, "live_profile": item["profile"],
        "private_profile": check_item["profile"], "source_attempt_ids": sorted(attempt_ids),
        "copied_rows": counts, "private_db_bytes": private_db.stat().st_size,
        "before": {"status": node["status"], "score": node.get("score"),
                   "attempt_id": node["attempt_id"], "source_sha256": node["source_sha256"]},
        "after": {"status": resulting["status"], "score": resulting.get("score"),
                  "attempt_id": resulting.get("attempt_id"),
                  "source_sha256": resulting.get("source_sha256")},
        "job": jobs[-1] if receipt else None,
        "receipt": str(receipt) if receipt else None,
        "last_session": (finished.get("fast_metrics") or {}).get("last_session"),
        "fast_inflight": len(finished.get("fast_inflight") or []),
        "error": error,
    }
    if receipt and receipt.exists():
        raw = json.loads(receipt.read_text())
        result["result"] = {key: raw.get(key) for key in (
            "status", "exact", "binary_type_revision", "attempt_id", "score",
            "source_sha256", "best_score_improved", "calls_attempted")}
        result["result"]["private_lineage"] = raw.get("private_lineage")
        result["result"]["context_status"] = [
            {key: row.get(key) for key in ("label", "status", "source_sha256")}
            for row in raw.get("context", []) if isinstance(row, dict)]
    (out / "retry-summary.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--function", default="Fcutoff")
    args = parser.parse_args()
    result = run(args.state, args.out, args.function)
    print(json.dumps({key: result[key] for key in (
        "function", "live_profile", "private_profile", "before", "after",
        "last_session", "fast_inflight", "error", "receipt")}, indent=2))
    return 0 if result["error"] is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
