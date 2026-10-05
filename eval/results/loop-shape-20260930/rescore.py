"""Re-score every unsolved function's best candidate once under today's toolchain. One compile each, no edits.

The near-miss search found functions whose recorded best attempt is complete when compiled again: headers, recipes and
frontend checks changed after the attempt was logged. This sweep counts those directly. Trial DB only; the sealed 50
are excluded.
"""
import json
import multiprocessing
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
REPO = Path("/home/grant/decomp/sbk1")
TRIAL = Path("/home/grant/decomp/runs/loop-shape-20260930/rescore.sqlite")


def frame():
    from eval import draft_census
    from solver import signals
    sealed = {x["name"] for x in json.loads((ROOT / "eval/results/heldout50-20260929/frame.json").read_text())}
    solved = draft_census.solved_by_pipeline()
    best = {}
    for path in draft_census.LEDGERS:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        for name, aid, diff in db.execute("select f.name, a.id, a.diff_summary from attempts a join functions f on "
                                          "f.addr=a.func_addr where a.compiled=1 and coalesce(a.exact,0)=0"):
            if name in solved or name in sealed:
                continue
            g = signals.distances(diff or "")
            if name not in best or g < best[name][0]:
                best[name] = (g, str(path), aid)
    return [{"name": n, "ledger": p, "attempt_id": a, "gradient": list(g)} for n, (g, p, a) in best.items()]


def one(item):
    from solver import site_edits, workspace
    db = sqlite3.connect(f"file:{item['ledger']}?mode=ro", uri=True)
    source = db.execute("select source_code from attempts where id=?", (item["attempt_id"],)).fetchone()[0]
    db.close()
    conn = sqlite3.connect(TRIAL, timeout=600)
    try:
        ws = workspace.bootstrap(REPO, item["name"])
        a = workspace.score(ws, REPO, f"{item['name']}_rescore_{time.time_ns()}", source, conn=conn,
                            func=item["name"], strategy="rescore:best", run_kind="rescore")
        conn.commit()
        fb = (a.verification or {}).get("function_boundary") or {}
        return {**item, "exact": workspace.repair_complete(a), "object_exact": bool(a.exact),
                "compiled": bool(a.compiled), "now": list(site_edits.gradient(a)),
                "function_exact": fb.get("function_exact") is True, "fb_schema": fb.get("schema_version"),
                "fb_error": fb.get("schema_3_error") or fb.get("error"),
                "jump_table": any(s.get("length") and s.get("candidate_section") for s in fb.get("data_sites") or [])}
    except Exception as exc:
        return {**item, "error": repr(exc)[:200]}
    finally:
        conn.close()


def main():
    TRIAL.parent.mkdir(parents=True, exist_ok=True)
    if not TRIAL.exists():
        conn = sqlite3.connect(TRIAL)
        conn.executescript((ROOT / "kb/schema.sql").read_text())
        conn.execute("ATTACH DATABASE ? AS c", ("file:/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite?mode=ro",))
        for table in ("extraction", "tus", "functions"):
            conn.execute(f"INSERT INTO main.{table} SELECT * FROM c.{table}")
        conn.commit()
        conn.close()
    items = frame()
    out = HERE / (sys.argv[2] if len(sys.argv) > 2 else "rescore.jsonl")
    done = {json.loads(l)["name"] for l in out.read_text().splitlines()} if out.exists() else set()
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    print(f"frame {len(items)}", flush=True)
    with multiprocessing.Pool(workers) as pool, out.open("a") as fh:
        for row in pool.imap_unordered(one, [i for i in items if i["name"] not in done]):
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            if row.get("exact") or row.get("function_exact"):
                print("EXACT" if row.get("exact") else "FUNCTION_EXACT", row["name"], row["gradient"], flush=True)


if __name__ == "__main__":
    main()
