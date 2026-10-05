"""Which target rodata label does each candidate address-taken rodata site / unresolved extern correspond to?"""
import json, sqlite3, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent; ROOT = HERE.parents[2]; sys.path.insert(0, str(ROOT))
from solver import workspace, byte_certificate as bc, function_boundary as fb
REPO = Path("/home/grant/decomp/sbk1"); TRIAL = "/home/grant/decomp/runs/lead3-20260930/trial.sqlite"
rows = {json.loads(l)["name"]: json.loads(l) for l in (HERE / "census.jsonl").read_text().splitlines()}
for name in sys.argv[1:]:
    r = rows[name]
    src = sqlite3.connect(f"file:{r['ledger']}?mode=ro", uri=True).execute("select source_code from attempts where id=?", (r["attempt_id"],)).fetchone()[0]
    ws = workspace.bootstrap(REPO, name); n = f"{name}_lp_{time.time_ns()}"
    conn = sqlite3.connect(TRIAL, timeout=600)
    workspace.score(ws, REPO, n, src, conn=conn, func=name, strategy="lead3:labels", run_kind="lead3"); conn.commit()
    t, c = (ws / "target.o").read_bytes(), (ws / f"{n}.o").read_bytes()
    tl = fb._data_symbols(t)
    print("==", name, "target rodata labels:", tl)
    asm = (ws / "target.s").read_text()
    print("   dlabels in target.s:", fb.DATA_LABEL.findall(asm)[:8])
    ti = bc.object_image(t)["sections"][".text"]["relocations"]; ci = bc.object_image(c)["sections"][".text"]["relocations"]
    tmap = {at: tuple(i) for at, k, i in ti if k == 5}
    for at, k, i in ci:
        if k == 5 and (i[0] == "section" and i[1] != ".text" or i[0] == "external"):
            tt = tmap.get(at)
            if tt and tt[0] == "section" and tt[1] in fb.DATA_SECTIONS or (i[0] == "section"):
                print(f"   @{at}: cand {tuple(i)}  target {tt}")
    for p in ws.glob(f"{n}*"): p.unlink()
