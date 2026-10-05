"""Chain (copy repair -> all copy-back merges) then hand the register-only residue to regalloc_search."""
import json, sqlite3, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from solver import unaligned_copy, temp_copyback, workspace, regalloc_search, residual_classes as rc
REPO = Path("/home/grant/decomp/sbk1")
conn = sqlite3.connect("/home/grant/decomp/runs/unaligned-copy-20260929/trial.sqlite")
LEDGERS = [Path("/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite"), Path("/home/grant/decomp/kb-sbk1.sqlite")]
out = []
for fn in sys.argv[1:]:
    best = None
    for p in LEDGERS:
        db = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
        r = db.execute("select a.score, a.source_code from attempts a join functions f on f.addr=a.func_addr where f.name=? "
                       "and a.compiled=1 and a.exact=0 order by a.score desc limit 1", (fn,)).fetchone()
        if r and (best is None or r[0] > best[0]): best = r
    ws = workspace.bootstrap(REPO, fn); run_id = f"copy-finish-{int(time.time())}-{fn}"
    def score(code, label, parent=None):
        return workspace.score(ws, REPO, f"{fn}_finish_{time.time_ns()}", code, conn=conn, func=fn,
                               strategy=f"copy-finish:{label}"[:120], run_id=run_id, run_kind="copy-finish")
    a = score(best[1], "baseline")
    v = list(unaligned_copy.variants(best[1], fn, a.diff or ""))
    if not v:
        out.append({"function": fn, "copy": None}); print(fn, "no copy"); continue
    code = v[0][1]; a = score(code, v[0][0])
    trail = [("copy", a.score, [rc.counts(a.diff)[c] for c in rc.CLASSES])]
    for _ in range(4):
        cands = [(l, c, score(c, l)) for l, c in temp_copyback.variants(code, fn, a.diff or "")]
        cands = [x for x in cands if x[2].compiled and x[2].score > a.score]
        if not cands: break
        l, code, a = max(cands, key=lambda x: x[2].score)
        trail.append((l, a.score, [rc.counts(a.diff)[c] for c in rc.CLASSES]))
    row = {"function": fn, "trail": trail, "exact_before_registers": workspace.repair_complete(a)}
    if not workspace.repair_complete(a):
        def compile_with_parent(candidate, label, parent_source):
            tag = f"{fn}_finishrs_{time.time_ns()}"
            att = workspace.score(ws, REPO, tag, candidate, conn=conn, func=fn, strategy=f"copy-finish:regalloc:{label}"[:120],
                                  run_id=run_id, run_kind="copy-finish")
            conn.commit()
            dump = ws / f"{tag}_object_dump_normalized.s"
            text = dump.read_text() if att.compiled and dump.is_file() else None
            evidence = {"compiled": bool(att.compiled), "score": att.score, "source_attribution": att.source_attribution,
                        "frontend": att.frontend, "compiler_recipe": att.compiler_recipe}
            return regalloc_search.Compiled(bool(att.compiled), workspace.repair_complete(att), text, att.diff or "", evidence)
        target = (ws / "target_object_dump_normalized.s").read_text()
        res = regalloc_search.search(fn, code, compile_with_parent, target, budget=120, enable=True,
                                     compile_with_parent=compile_with_parent, coalesce=True)
        row["regalloc"] = {"exact": bool(res.exact), **res.summary()}
        if res.exact:
            row["exact_source"] = getattr(res, "source", None)
    conn.commit(); out.append(row); print(json.dumps(row, default=str)[:900], flush=True)
Path(__file__).with_name("finish.json").write_text(json.dumps(out, indent=1, default=str))
