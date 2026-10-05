"""Raw non-.text bytes for the text-identical hidden-difference functions (census.jsonl text_sha_equal)."""
import json, sqlite3, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent; ROOT = HERE.parents[2]; sys.path.insert(0, str(ROOT))
from solver import workspace, byte_certificate as bc
REPO = Path("/home/grant/decomp/sbk1"); TRIAL = "/home/grant/decomp/runs/lead3-20260930/trial.sqlite"
rows = [json.loads(l) for l in (HERE / "census.jsonl").read_text().splitlines()]
for r in rows:
    if not (r.get("text_sha_equal") and r.get("nontext")): continue
    src = sqlite3.connect(f"file:{r['ledger']}?mode=ro", uri=True).execute(
        "select source_code from attempts where id=?", (r["attempt_id"],)).fetchone()[0]
    ws = workspace.bootstrap(REPO, r["name"]); name = f"{r['name']}_b8_{time.time_ns()}"
    conn = sqlite3.connect(TRIAL, timeout=600)
    workspace.score(ws, REPO, name, src, conn=conn, func=r["name"], strategy="lead3:bytes", run_kind="lead3"); conn.commit()
    t = bc.section_contents((ws / "target.o").read_bytes()); c = bc.section_contents((ws / f"{name}.o").read_bytes())
    ti = bc.object_image((ws / "target.o").read_bytes())["sections"]; ci = bc.object_image((ws / f"{name}.o").read_bytes())["sections"]
    print("==", r["name"], r["attempt_id"], r["score"])
    for s in sorted((set(t) | set(c)) & {".rodata", ".data", ".bss", ".late_rodata"}):
        print(f"   {s:8} T[{len(t.get(s,b''))}] {t.get(s,b'').hex()[:120]}")
        print(f"   {s:8} C[{len(c.get(s,b''))}] {c.get(s,b'').hex()[:120]}")
    tr = [x for x in ti[".text"]["relocations"]]; cr = [x for x in ci[".text"]["relocations"]]
    dif = [(a, b) for a, b in zip(tr, cr) if a != b]
    print("   text relocs differing (first 4):", dif[:4])
    ex = [l for l in src.splitlines() if l.startswith("extern") and ("D_" in l or "char" in l)][:4]
    print("   extern data decls:", ex)
    for p in ws.glob(f"{name}*"): p.unlink()
