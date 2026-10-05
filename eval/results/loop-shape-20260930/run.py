"""L1: raw binary-type drafts vs raw + loop_shape variants. See PREREGISTRATION.md.

    cd /mnt/c/Code/gameDecomp && ~/decomp/sbk1/.venv/bin/python eval/results/loop-shape-20260930/run.py --workers 8
"""
from __future__ import annotations

import argparse
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
TRIAL = Path("/home/grant/decomp/runs/loop-shape-20260930/l1.sqlite")
CAMPAIGN = Path("/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite")


def frame() -> list[str]:
    from eval import draft_census
    sealed = {x["name"] for x in json.loads((ROOT / "eval/results/heldout50-20260929/frame.json").read_text())}
    solved = draft_census.solved_by_pipeline()
    rows = [json.loads(l) for l in (ROOT / "eval/results/draft-census-20260930/rows.jsonl").read_text().splitlines()]
    out = [r["function"] for r in rows if "draft" in r and r["function"] not in solved and r["function"] not in sealed
           and (r["draft"]["goto"] or r["draft"]["do"] or r["draft"]["while"])]
    return sorted(out)


def init_db():
    TRIAL.parent.mkdir(parents=True, exist_ok=True)
    if TRIAL.exists():
        return
    conn = sqlite3.connect(TRIAL)
    conn.executescript((ROOT / "kb/schema.sql").read_text())
    conn.execute("ATTACH DATABASE ? AS c", (CAMPAIGN.as_uri() + "?mode=ro",))
    for table in ("extraction", "tus", "functions"):
        conn.execute(f"INSERT INTO main.{table} SELECT * FROM c.{table}")
    conn.commit()
    conn.execute("DETACH DATABASE c")
    conn.close()


def one(fn: str) -> dict:
    from solver import binary_type_draft, loop_shape, site_edits, skeleton, workspace
    row = {"function": fn, "raw": [], "loops": []}
    conn = sqlite3.connect(TRIAL, timeout=600)
    started = time.time()
    try:
        ws = workspace.bootstrap(REPO, fn)
        drafts, _reports = binary_type_draft.variants(REPO, fn, ws)
        row["drafts"] = len(drafts)
        run_id = f"l1-{int(time.time())}-{fn}"

        def score(code, label):
            a = workspace.score(ws, REPO, f"{fn}_l1_{time.time_ns()}", code, conn=conn, func=fn,
                                strategy=f"l1:{label}"[:120], run_id=run_id, run_kind="l1")
            conn.commit()
            rec = {"label": label, "compiled": bool(a.compiled), "exact": workspace.repair_complete(a),
                   "gradient": list(site_edits.gradient(a)), "score": a.score}
            if a.compiled:
                rec["skeleton"] = 0 if rec["exact"] else skeleton.distance(a.diff or "")
            return rec
        seen = set()
        for label, src in drafts:
            row["raw"].append(score(src, label))
            variants = loop_shape.variants(src, fn)
            row.setdefault("variants_per_draft", []).append(len(variants))
            for vlabel, new in variants:
                if new in seen:
                    continue
                seen.add(new)
                row["loops"].append(score(new, f"{label}|{vlabel}"))
    except Exception as exc:
        row["error"] = repr(exc)[:300]
    finally:
        conn.close()
    row["seconds"] = round(time.time() - started, 1)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    init_db()
    names = frame()
    if args.limit:
        names = names[:args.limit]
    out = HERE / "l1.jsonl"
    done = {json.loads(l)["function"] for l in out.read_text().splitlines()} if out.exists() else set()
    print(f"frame {len(names)}, remaining {len([n for n in names if n not in done])}", flush=True)
    with multiprocessing.Pool(args.workers) as pool, out.open("a") as fh:
        for row in pool.imap_unordered(one, [n for n in names if n not in done]):
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            best = lambda arm: min((tuple(r["gradient"]) for r in row.get(arm, [])), default=None)
            print(row["function"], "raw", best("raw"), "loops", best("loops"), row.get("error", ""), flush=True)


if __name__ == "__main__":
    main()
