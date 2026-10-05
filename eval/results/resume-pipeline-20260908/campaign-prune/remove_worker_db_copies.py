"""Remove the three worker DB copies so the next dispatch rebuilds them from the packed campaign DB.

Each worker.sqlite is a full copy of campaign.sqlite that `campaign_workers.synchronize` creates when it is
missing (fast_campaign.py: `synchronize(args.db, slot/'worker.sqlite', prior)`), so removing one costs a
copy at the next dispatch, never data. Refuses unless every row a worker holds past its recorded cutoff (its
last job's local work) is already in the campaign DB with the same run, source, score and exact flag, and
the campaign is paused. After `eval.attempt_compact` packed the campaign DB (2026-09-22), the copies
rebuilt from it are about half the size; the old ones would keep the unpacked rows forever.
"""
import json
from pathlib import Path
import sqlite3
import time

WORKERS = Path.home() / "decomp/campaign-workers-20260911"
MAIN = Path.home() / "decomp/runs/resume-pipeline-20260908/campaign.sqlite"
CONTROL = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")


def main():
    assert (CONTROL / "service.pause").exists(), "campaign must stay paused"
    main_db = sqlite3.connect(f"file:{MAIN}?mode=ro", uri=True)
    removed = []
    for slot in sorted(p for p in WORKERS.iterdir() if (p / "worker.sqlite").exists()):
        db_path = slot / "worker.sqlite"
        for suffix in ("-journal", "-wal"):
            assert not Path(str(db_path) + suffix).exists(), f"{db_path}{suffix} present"
        cutoff = json.loads((slot / "database-cutoffs.json").read_text())["attempts"]
        worker = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        local = worker.execute("SELECT run_id, source_sha256, score, exact FROM attempts WHERE id > ?", (cutoff,)).fetchall()
        worker.close()
        for run_id, sha, score, exact in local:
            hit = main_db.execute("SELECT 1 FROM attempts WHERE run_id IS ? AND source_sha256 IS ? AND score IS ? AND exact IS ?",
                                  (run_id, sha, score, exact)).fetchone()
            assert hit, f"{slot.name}: local row {run_id}/{sha[:12]} not imported; refusing"
        size = db_path.stat().st_size
        db_path.unlink()
        removed.append({"slot": slot.name, "bytes": size, "local_rows_verified_imported": len(local)})
        print(json.dumps(removed[-1]), flush=True)
    receipt = {"kind": "remove-worker-db-copies", "at": time.time(), "removed": removed,
               "freed_gb": round(sum(r["bytes"] for r in removed) / 1e9, 2)}
    (CONTROL / "campaign-prune" / f"{time.time_ns()}-remove-worker-dbs.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps({"freed_gb": receipt["freed_gb"]}))


if __name__ == "__main__":
    main()
