"""osMotorStart: copy repair, then every copy-back merge applied in sequence; compile each stage."""
import sqlite3, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from solver import unaligned_copy, temp_copyback, workspace, residual_classes as rc
REPO = Path("/home/grant/decomp/sbk1")
conn = sqlite3.connect("/home/grant/decomp/runs/unaligned-copy-20260929/trial.sqlite")
fn = sys.argv[1] if len(sys.argv) > 1 else "osMotorStart"
db = sqlite3.connect("file:/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite?mode=ro", uri=True)
src = db.execute("select a.source_code from attempts a join functions f on f.addr=a.func_addr where f.name=? and a.compiled=1 "
                 "and a.exact=0 order by a.score desc limit 1", (fn,)).fetchone()[0]
ws = workspace.bootstrap(REPO, fn); run_id = f"copy-both-{int(time.time())}-{fn}"
def score(code, label):
    return workspace.score(ws, REPO, f"{fn}_both_{time.time_ns()}", code, conn=conn, func=fn,
                           strategy=f"copy-both:{label}", run_id=run_id, run_kind="copy-both")
a = score(src, "baseline")
code = next(iter(unaligned_copy.variants(src, fn, a.diff)))[1]
a = score(code, "copy")
print("copy", a.score, [rc.counts(a.diff)[c] for c in rc.CLASSES])
for step in range(4):
    v = list(temp_copyback.variants(code, fn, a.diff or ""))
    if not v: break
    best = None
    for label, cand in v:
        b = score(cand, label)
        if b.compiled and (best is None or b.score > best[1].score): best = (cand, b, label)
    if best is None or best[1].score <= a.score: break
    code, a = best[0], best[1]
    print(best[2], a.score, [rc.counts(a.diff)[c] for c in rc.CLASSES])
conn.commit()
frame = [l for l in (a.diff or "").splitlines() if "sp,sp,-" in l]
print("frame lines:", frame)
i = code.find(fn + "("); print(code[i:i + 700])
