"""Unsolved functions with a 100-score, empty-diff attempt: re-score once and record which acceptance check fails."""
import json, sqlite3, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from solver import workspace
REPO = Path("/home/grant/decomp/sbk1")
C = Path("/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite"); K = Path("/home/grant/decomp/kb-sbk1.sqlite")
TRIAL = Path("/home/grant/decomp/runs/perfect-score-20260929/trial.sqlite")
if not TRIAL.exists():
    TRIAL.parent.mkdir(parents=True, exist_ok=True)
    t = sqlite3.connect(TRIAL); t.executescript((ROOT / "kb/schema.sql").read_text())
    t.execute("ATTACH DATABASE ? AS c", (C.as_uri() + "?mode=ro",))
    for tb in ("extraction", "tus", "functions"): t.execute(f"INSERT INTO main.{tb} SELECT * FROM c.{tb}")
    t.commit(); t.execute("DETACH DATABASE c"); t.close()
exact, cands = set(), {}
for p in (C, K):
    db = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
    exact |= {r[0] for r in db.execute("select f.name from functions f join attempts a on a.func_addr=f.addr where a.exact=1")}
for p in (C, K):
    db = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
    for n, aid, src, st in db.execute("select f.name, a.id, a.source_code, a.strategy from attempts a join functions f on f.addr=a.func_addr "
                                      "where a.compiled=1 and a.exact=0 and a.score>=99.999 order by a.id desc"):
        if n not in exact and n not in cands:
            cands[n] = {"ledger": p.name, "attempt_id": aid, "strategy": st, "source": src}
conn = sqlite3.connect(TRIAL)
out = []
for n, c in sorted(cands.items()):
    try:
        ws = workspace.bootstrap(REPO, n)
        a = workspace.score(ws, REPO, f"{n}_perfect_{time.time_ns()}", c["source"], conn=conn, func=n,
                            strategy="perfect-score:rescore", run_id=f"perfect-score-{n}", run_kind="perfect-score")
        conn.commit()
        row = {"function": n, "ledger": c["ledger"], "attempt_id": c["attempt_id"], "strategy": c["strategy"],
               "compiled": a.compiled, "score": a.score, "exact": a.exact, "complete": workspace.repair_complete(a),
               "frontend": a.frontend, "verification": a.verification,
               "raw_tail": (a.raw_output or "")[-600:]}
    except Exception as exc:
        row = {"function": n, "error": repr(exc)[:300]}
    out.append(row)
    print(n, row.get("score"), "exact", row.get("exact"), "complete", row.get("complete"),
          "frontend", (row.get("frontend") or {}).get("passed"), row.get("error", ""), flush=True)
Path(__file__).with_name("diagnose.json").write_text(json.dumps(out, indent=1, default=str))
