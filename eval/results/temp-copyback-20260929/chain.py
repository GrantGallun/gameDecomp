"""best state -> unaligned_copy -> temp_copyback (each variant compiled once) on the copy-class functions."""
import json, sqlite3, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from solver import unaligned_copy, temp_copyback, workspace, residual_classes
REPO = Path("/home/grant/decomp/sbk1")
TRIAL = Path("/home/grant/decomp/runs/unaligned-copy-20260929/trial.sqlite")      # same trial DB as the copy run
LEDGERS = [Path("/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite"), Path("/home/grant/decomp/kb-sbk1.sqlite")]
FRAME = ["osMotorStart", "osMotorStop", "__osContRamWrite", "__osContRamRead", "__osContGetInitData", "osContGetReadData"]
conn = sqlite3.connect(TRIAL)
frame_of = lambda d: next((x for x in (d or "").splitlines() if "addiu" in x and "sp,sp,-" in x), None)
out = []
for fn in FRAME:
    best = None
    for p in LEDGERS:
        db = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
        r = db.execute("select a.score, a.source_code from attempts a join functions f on f.addr=a.func_addr where f.name=? "
                       "and a.compiled=1 and a.exact=0 order by a.score desc limit 1", (fn,)).fetchone()
        if r and (best is None or r[0] > best[0]): best = r
    ws = workspace.bootstrap(REPO, fn); run_id = f"copy-chain-{int(time.time())}-{fn}"
    score = lambda code, label: workspace.score(ws, REPO, f"{fn}_chain_{time.time_ns()}", code, conn=conn, func=fn,
                                                strategy=f"copy-chain:{label}", run_id=run_id, run_kind="copy-chain")
    base = score(best[1], "baseline")
    label, copied = next(iter(unaligned_copy.variants(best[1], fn, base.diff or "")), (None, None))
    if not copied: out.append({"function": fn, "copy": None}); continue
    a1 = score(copied, label)
    row = {"function": fn, "baseline": base.score, "copy": a1.score, "copy_frame": frame_of(a1.diff), "merges": []}
    for l2, merged in temp_copyback.variants(copied, fn, a1.diff or ""):
        a2 = score(merged, l2)
        row["merges"].append({"label": l2, "compiled": bool(a2.compiled), "score": a2.score, "exact": workspace.repair_complete(a2),
                              "classes": residual_classes.counts(a2.diff) if a2.compiled else (a2.compiler_stderr or "")[-200:]})
    conn.commit(); out.append(row); print(json.dumps(row)[:700], flush=True)
Path(__file__).with_name("chain.json").write_text(json.dumps(out, indent=1))
