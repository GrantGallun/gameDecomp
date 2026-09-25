"""Budgeted greedy search over binary-typed drafts that compile but do not match (PROTOCOL-search.md).

From each compiled draft (capability run rows), the project's own deterministic stream
`solver.regalloc_mutations.variants` (branch-shape, storage, representation, owner rewrites, ...) proposes children;
each is compiled in the copied workspace with the recorded recipe; the best improving child becomes the parent.
Budget per function: 32 compiles, 8 children per step. Stop at exact, at a step with no improvement, or at the budget.
Then one restart from the best node with a fresh 16-compile budget (restart-20260923: restarting beat one long search).

    python3 search.py [--jobs 4] [--runs v1,v2]   -> E/search/<fn>.json, search-summary.json here
"""
import collections
import concurrent.futures
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ABL = HERE.parent / "context-ablation-20260924"
sys.path.insert(0, str(ABL))
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
import ablate  # noqa: E402
from ablate import pairs, mine  # noqa: E402
from solver import regalloc_mutations  # noqa: E402

E = Path.home() / "decomp/experiments/binary-types-capability-20260924"
PER_STEP, BUDGET, RESTART = 8, 32, 16


def recipe(ws: Path) -> dict | None:
    for p in ws.glob(".compiler-*.json"):
        if p.name != ".compiler-target.json":
            try:
                return json.loads(p.read_text())
            except ValueError:
                return None
    return None


def build(ws, name, source):
    b = pairs.build(ws, "srch", source)
    if not b["compiled"]:
        return None
    t = mine.mask((ws / "target_object_dump_normalized.s").read_text())
    return {"score": b["score"] or 0.0, "exact": mine.mask(b["dump"]) == t, "diff": b.get("diff") or ""}


def climb(ws, name, source, verdict, budget, evidence, log):
    best_src, best = source, verdict
    used, seen = 0, {source}
    while used < budget and not best["exact"]:
        children = []
        try:
            for label, kind, cand in regalloc_mutations.variants(best_src, name, best["diff"], evidence=evidence):
                if cand in seen:
                    continue
                seen.add(cand)
                children.append((label, kind, cand))
                if len(children) >= PER_STEP:
                    break
        except Exception as exc:                    # a family crash declines the step; recorded
            log.append({"decline": repr(exc)[-200:]})
        if not children:
            break
        step_best = None
        for label, kind, cand in children:
            if used >= budget:
                break
            v = build(ws, name, cand)
            used += 1
            if v and (step_best is None or (v["exact"], v["score"]) > (step_best[1]["exact"], step_best[1]["score"])):
                step_best = (cand, v, label, kind)
        if step_best is None or (step_best[1]["exact"], step_best[1]["score"]) <= (best["exact"], best["score"]):
            break
        best_src, best = step_best[0], step_best[1]
        log.append({"label": step_best[2], "kind": step_best[3], "score": best["score"], "exact": best["exact"]})
    return best_src, best, used


def one(mirror, row):
    name = row["function"]
    ws = pairs.workspace(mirror, name)
    out = {"function": name, "start": row.get("score")}
    v = build(ws, name, row["source"])
    if v is None:
        return out | {"status": "baseline-not-compiled"}
    evidence = {"compiler_recipe": recipe(ws)}
    log = []
    src, best, used = climb(ws, name, row["source"], v, BUDGET, evidence, log)
    if not best["exact"] and used:
        log.append({"restart": True})
        src, best, used2 = climb(ws, name, src, best, RESTART, evidence, log)
        used += used2
    return out | {"status": "exact" if best["exact"] else "improved" if best["score"] > (row.get("score") or 0) else "flat",
                  "end": best["score"], "compiles": used, "path": log,
                  "source": src if best["exact"] else None,
                  "best_source": src if not best["exact"] and best["score"] > (row.get("score") or 0) else None}


def main():
    jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 4
    runs = (sys.argv[sys.argv.index("--runs") + 1] if "--runs" in sys.argv else "v1,v2").split(",")
    dirs = {"v1": E / "rows", "v2": E / "v2" / "rows", "v3": E / "v3" / "rows"}
    rows = {}
    for r in runs:                                   # later runs override earlier ones for the same function
        for p in sorted(dirs[r].glob("*.json")):
            x = json.loads(p.read_text())
            if x.get("status") == "compiled" and x.get("source"):
                rows[x["function"]] = x
            elif x.get("status") == "exact":
                rows.pop(x["function"], None)
    out_dir = E / "search"
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = [r for n, r in sorted(rows.items()) if not (out_dir / f"{n}.json").exists()]
    print(len(rows), "compiled-not-exact;", len(todo), "to search", flush=True)
    mirror = pairs.mirror_repo()
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        for i, res in enumerate(pool.map(lambda r: one(mirror, r), todo), 1):
            (out_dir / f"{res['function']}.json").write_text(json.dumps(res))
            if i % 25 == 0:
                print(i, flush=True)
    done = [json.loads((out_dir / f"{n}.json").read_text()) for n in rows]
    c = collections.Counter(d["status"] for d in done)
    kinds = collections.Counter(s["kind"] for d in done if d["status"] == "exact" for s in d["path"] if "kind" in s)
    s = {"searched": len(done), **c, "exact_functions": sorted(d["function"] for d in done if d["status"] == "exact"),
         "families_on_exact_paths": dict(kinds.most_common())}
    (HERE / "search-summary.json").write_text(json.dumps(s, indent=1))
    print(json.dumps({k: v for k, v in s.items() if k != "exact_functions"}, indent=1))


if __name__ == "__main__":
    main()
