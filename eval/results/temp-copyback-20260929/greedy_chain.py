"""Greedy chain of the three new generators (unaligned_copy, temp_copyback, counted_loop) from each best state.

Each step compiles every variant the generators offer on the current state and keeps the best one that
raises the score; stops when none does. Logged to the unaligned-copy trial DB (run_kind greedy-chain).
Prediction (written before the run): osMotorStart/osMotorStop exact from their raw drafts; 0-3 others.
"""
import json, multiprocessing, sqlite3, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
REPO = Path("/home/grant/decomp/sbk1")
TRIAL = "/home/grant/decomp/runs/unaligned-copy-20260929/trial.sqlite"
LEDGERS = [Path("/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite"), Path("/home/grant/decomp/kb-sbk1.sqlite")]


def one(fn):
    from solver import unaligned_copy, temp_copyback, counted_loop, workspace, residual_classes as rc
    conn = sqlite3.connect(TRIAL, timeout=600)
    best = None
    for p in LEDGERS:
        db = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
        r = db.execute("select a.score, a.source_code from attempts a join functions f on f.addr=a.func_addr where f.name=? "
                       "and a.compiled=1 and a.exact=0 order by a.score desc limit 1", (fn,)).fetchone()
        if r and (best is None or r[0] > best[0]): best = r
    ws = workspace.bootstrap(REPO, fn); run_id = f"greedy-chain-{int(time.time())}-{fn}"
    def score(code, label):
        a = workspace.score(ws, REPO, f"{fn}_gchain_{time.time_ns()}", code, conn=conn, func=fn,
                            strategy=f"greedy-chain:{label}"[:120], run_id=run_id, run_kind="greedy-chain")
        conn.commit(); return a
    code = best[1]; cur = score(code, "baseline")
    trail = [("baseline", cur.score)]
    compiles = 1
    for step in range(8):
        if workspace.repair_complete(cur): break
        cands = []
        for gen in (unaligned_copy, temp_copyback, counted_loop):
            try:
                cands += list(gen.variants(code, fn, cur.diff or ""))
            except Exception as exc:
                trail.append(("generator-error", f"{gen.__name__}: {exc!r}"[:120]))
        results = []
        for label, cand in cands:
            a = score(cand, label); compiles += 1
            if a.compiled: results.append((a.score, label, cand, a))
        better = [r for r in results if r[0] > cur.score or workspace.repair_complete(r[3])]
        if not better: break
        s, label, code, cur = max(better, key=lambda r: (workspace.repair_complete(r[3]), r[0]))
        trail.append((label, s))
    conn.close()
    return {"function": fn, "exact": workspace.repair_complete(cur), "score": cur.score, "compiles": compiles,
            "classes": [rc.counts(cur.diff)[c] for c in rc.CLASSES] if cur.compiled and cur.diff else None,
            "trail": trail, "source": code if workspace.repair_complete(cur) else None}


if __name__ == "__main__":
    fired = json.loads(Path(__file__).with_name("loop_census.json").read_text())
    frame = sorted(set(fired) | {"osMotorStart", "osMotorStop", "__osContRamWrite", "__osContRamRead",
                                 "__osContGetInitData", "osContGetReadData"})
    with multiprocessing.Pool(6) as pool:
        rows = pool.map(one, frame)
    for r in rows:
        print(f"{r['function']:44s} exact={r['exact']} {r['trail'][0][1]} -> {r['score']} compiles={r['compiles']} {r['classes']}  "
              f"{[t[0].split(':')[0] for t in r['trail'][1:]]}")
    Path(__file__).with_name("greedy_chain.json").write_text(json.dumps(rows, indent=1, default=str))
