"""Compile solver.unaligned_copy's variant on the best state of each packed-word function (one compile each)."""
import json, sqlite3, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from solver import unaligned_copy, workspace, residual_classes, diffrepair
REPO = Path("/home/grant/decomp/sbk1")
TRIAL = Path("/home/grant/decomp/runs/unaligned-copy-20260929/trial.sqlite")
LEDGERS = [Path("/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite"), Path("/home/grant/decomp/kb-sbk1.sqlite")]
FRAME = ["osPfsFindFile", "__osContRamWrite", "osMotorStop", "osMotorStart", "__osContRamRead",
         "__osContGetInitData", "__osPfsGetInitData", "updateRaceResultsFlow", "osContGetReadData"]
if not TRIAL.exists():
    TRIAL.parent.mkdir(parents=True, exist_ok=True)
    t = sqlite3.connect(TRIAL); t.executescript((ROOT / "kb/schema.sql").read_text())
    t.execute("ATTACH DATABASE ? AS c", (LEDGERS[0].as_uri() + "?mode=ro",))
    for tb in ("extraction", "tus", "functions"): t.execute(f"INSERT INTO main.{tb} SELECT * FROM c.{tb}")
    t.commit(); t.execute("DETACH DATABASE c"); t.close()
conn = sqlite3.connect(TRIAL)
lw = lambda d, side: sum(1 for x in diffrepair._streams(d or "")[side] if x.split() and x.split()[0] in ("lwl", "lwr"))
out = []
for fn in FRAME:
    best = None
    for p in LEDGERS:
        db = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
        r = db.execute("select a.score, a.source_code from attempts a join functions f on f.addr=a.func_addr where f.name=? "
                       "and a.compiled=1 and a.exact=0 order by a.score desc limit 1", (fn,)).fetchone()
        if r and (best is None or r[0] > best[0]): best = r
    ws = workspace.bootstrap(REPO, fn)
    run_id = f"unaligned-copy-{int(time.time())}-{fn}"
    base = workspace.score(ws, REPO, f"{fn}_ucopy_{time.time_ns()}", best[1], conn=conn, func=fn,
                           strategy="unaligned-copy:baseline", run_id=run_id, run_kind="unaligned-copy")
    row = {"function": fn, "baseline": base.score, "base_lwl": [lw(base.diff, 0), lw(base.diff, 1)], "variants": []}
    for label, cand in unaligned_copy.variants(best[1], fn, base.diff or ""):
        a = workspace.score(ws, REPO, f"{fn}_ucopy_{time.time_ns()}", cand, conn=conn, func=fn,
                            strategy=f"unaligned-copy:{label}", run_id=run_id, run_kind="unaligned-copy")
        row["variants"].append({"label": label, "compiled": bool(a.compiled), "score": a.score,
                                "exact": workspace.repair_complete(a), "lwl": [lw(a.diff, 0), lw(a.diff, 1)] if a.compiled else None,
                                "classes": residual_classes.counts(a.diff) if a.compiled else None,
                                "stderr": None if a.compiled else (a.compiler_stderr or "")[-400:]})
    conn.commit()
    out.append(row); print(json.dumps(row)[:600], flush=True)
(Path(__file__).with_name("results.json")).write_text(json.dumps(out, indent=1))
