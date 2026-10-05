"""End to end on real objects: the object layer's route for every unsolved best candidate, and each lever applied.

workspace.score now attaches `attempt.object` (object_discrepancy.summarize). For each function of the census frame:
score the best candidate, record the route, and when the route names a lever run that generator on the fresh objects
and score its rewrite. Reports object-exact and function-exact (schema 3) verdicts. Trial DB only.

    python3 eval/results/hidden-object-20260930/layer_run.py [workers]
"""
import json, multiprocessing, sqlite3, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
REPO = Path("/home/grant/decomp/sbk1")
TRIAL = "/home/grant/decomp/runs/lead3-20260930/trial.sqlite"


def verdict(a):
    from solver import workspace
    v = a.verification or {}
    fb = v.get("function_boundary") or {}
    return {"compiled": a.compiled, "score": a.score, "object_exact": bool(a.exact),
            "complete": workspace.repair_complete(a), "function_exact": fb.get("function_exact"),
            "fb_error": fb.get("schema_3_error") or fb.get("error"),
            "route": (a.object or {}).get("route")}


def one(item):
    from solver import file_scope_objects, rodata_symbol, workspace
    try:
        src = sqlite3.connect(f"file:{item['ledger']}?mode=ro", uri=True).execute(
            "select source_code from attempts where id=?", (item["attempt_id"],)).fetchone()[0]
        ws = workspace.bootstrap(REPO, item["name"])
        conn = sqlite3.connect(TRIAL, timeout=600)
        tag = f"{item['name']}_layer_{time.time_ns()}"
        a = workspace.score(ws, REPO, tag, src, conn=conn, func=item["name"], strategy="lead3:layer",
                            run_kind="lead3")
        conn.commit()
        row = {"name": item["name"], "attempt_id": item["attempt_id"], "before": verdict(a),
               "object": a.object, "levers": []}
        for lever in (a.object or {}).get("levers", []):
            gen = {"rodata_address": rodata_symbol.address_variants,
                   "unused_file_scope_object": file_scope_objects.variants}[lever]
            for label, new in gen(src, item["name"], target_obj=ws / "target.o", candidate_obj=ws / f"{tag}.o"):
                b = workspace.score(ws, REPO, f"{tag}_{lever}", new, conn=conn, func=item["name"],
                                    strategy=f"lead3:{lever}", run_kind="lead3")
                conn.commit()
                row["levers"].append({"lever": lever, "label": label, "after": verdict(b)})
        for p in ws.glob(f"{tag}*"):
            try: p.unlink()
            except OSError: pass
        return row
    except Exception as exc:
        return {"name": item["name"], "error": repr(exc)[:300]}


def main():
    sys.path.insert(0, str(ROOT / "eval/results/loop-shape-20260930"))
    import rescore
    items = rescore.frame()
    out = HERE / "layer_run.jsonl"
    if len(sys.argv) > 2:                     # re-run: only functions a previous run routed away from c_edit
        prior = [json.loads(l) for l in (HERE / sys.argv[2]).read_text().splitlines()]
        keep = {r["name"] for r in prior if (r.get("object") or {}).get("route") not in (None, "c_edit")}
        items = [x for x in items if x["name"] in keep]
        out = HERE / sys.argv[3]
    done = {json.loads(l)["name"] for l in out.read_text().splitlines()} if out.exists() else set()
    print(f"frame {len(items)} done {len(done)}", flush=True)
    with multiprocessing.Pool(int(sys.argv[1]) if len(sys.argv) > 1 else 6) as pool, out.open("a") as fh:
        for i, row in enumerate(pool.imap_unordered(one, [x for x in items if x["name"] not in done])):
            fh.write(json.dumps(row) + "\n"); fh.flush()
            if i % 50 == 0: print(i, flush=True)


if __name__ == "__main__":
    main()
