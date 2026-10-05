"""Compile hand-written hypothesis sources in isolated workspaces; every call logged to probe.sqlite.

A probe tests one causal claim about a residual before any generator is written for it.
Usage (WSL): python3 probe.py probes.json   where probes.json is [{"function","label","source"}]
"""
import json
from pathlib import Path
import sqlite3
import sys

OUT = Path(__file__).resolve().parent
FROZEN = json.loads((OUT / "freeze.json").read_text())
CODE = Path(FROZEN["code_root"])
sys.path.insert(0, str(CODE))
from eval.campaign_workers import isolate  # noqa: E402
from eval.search_evolution import compile_logged  # noqa: E402
from eval.search_replay import digest  # noqa: E402

NATIVE = Path.home() / "decomp/experiments/population-transfer-20260922"
REPO = Path.home() / "decomp/sbk1"
DB = NATIVE / "probe.sqlite"


def main():
    probes = json.loads(Path(sys.argv[1]).read_text())
    fresh = not DB.exists()
    db = sqlite3.connect(DB, timeout=300)
    if fresh:
        db.executescript((CODE / "kb/schema.sql").read_text())
        upstream = sqlite3.connect(f"file:{FROZEN['kb']}?mode=ro", uri=True)
        for table in ("tus", "functions"):
            cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
            db.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                           upstream.execute(f"SELECT {','.join(cols)} FROM {table}"))
        db.commit()
    results = []
    for p in probes:
        name, source = p["function"], p["source"]
        repo = isolate(REPO, NATIVE / "probe-ws" / name / digest(source)[:12], name)
        v = compile_logged(repo / "nonmatchings" / name, repo, name, source, conn=db,
                           strategy=f"population-transfer:probe:{p['label']}", run_id=f"population-transfer:probe:{name}",
                           action=p["label"], model="hand-probe", prompt="hypothesis probe",
                           extra={"training_eligible": False})
        row = {"function": name, "label": p["label"], "compiled": v["compiled"], "exact": v["exact"],
               "score": v["score"], "receipt_id": v["receipt_id"], "source_sha256": digest(source),
               "diff": (v.get("diff") or "")[:1500], "stderr": (v.get("stderr") or "")[:600]}
        results.append(row)
        print(json.dumps({k: row[k] for k in ("function", "label", "compiled", "exact", "score")}), flush=True)
        if not v["exact"]:
            print(row["diff"] or row["stderr"], flush=True)
    out = OUT / "probes.jsonl"
    with out.open("a") as fh:
        for row in results:
            fh.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main()
