"""A/B of keyed resolution and site ranking inside the production register search.

Runs `solver.regalloc_search.search` -- the function the campaign calls, with `enable=True` as the campaign
runs it -- in three arms per function, each from its own baseline compile and at the same budget in
compile-equivalents:

    plain           the campaign's search today
    keyed           + key=ido_stages.optimizer_key (reuse a compile when the key matches)
    keyed+ranked    + rank_sites=True

Every compile is logged to a per-run attempts DB; keyed resolutions go to `model_proposals`. Preregistered
predictions: eval/results/regalloc-keyed-20260927/PREREGISTRATION.md.

    python3 -m eval.regalloc_keyed_ab --cohort COHORT.json --out DIR [--budget 200] [--workers 3]
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import sqlite3
import time
from pathlib import Path

REPO = Path.home() / "decomp/sbk1"
SCHEMA = Path(__file__).resolve().parents[1] / "kb/schema.sql"
ARMS = (("plain", False, False), ("keyed", True, False), ("keyed+ranked", True, True))


def run_function(row: dict, out: Path, budget: int, key_cost: float) -> dict:
    from eval import campaign_workers
    from kb.attempts import record_model_proposal
    from solver import ido_stages, regalloc_search, workspace
    name = row["name"]
    native = out / "native" / name
    iso = campaign_workers.isolate(REPO, native / "game", name)
    ws = iso / "nonmatchings" / name
    conn = sqlite3.connect(native / "attempts.sqlite")
    conn.executescript(SCHEMA.read_text())
    with sqlite3.connect(f"file:{(REPO.parent / 'kb-sbk1.sqlite').as_posix()}?mode=ro", uri=True) as kb:
        found = kb.execute("SELECT f.addr,f.size,t.name FROM functions f JOIN tus t ON t.id=f.tu_id "
                           "WHERE f.name=?", (name,)).fetchone()
    if found is None:
        return {"function": name, "status": "error", "error": "function missing from kb-sbk1"}
    conn.execute("INSERT OR IGNORE INTO tus(id,name) VALUES(1,?)", (found[2],))
    conn.execute("INSERT OR IGNORE INTO functions(addr,name,size,tu_id) VALUES(?,?,?,1)", (found[0], name, found[1]))
    conn.commit()
    source = Path(row["source"]).read_text()
    report = {"function": name, "status": "done", "arms": {}}
    for arm, use_key, rank in ARMS:
        run_id = f"regalloc-keyed-ab-{arm}-{name}"
        timing = {"compile_seconds": 0.0, "compiles": 0, "key_seconds": 0.0, "keys": 0}
        counter = [0]

        def compile_candidate(candidate, label):
            counter[0] += 1
            tag = f"{name}_{arm.replace('+', '_')}_{counter[0]}"
            started = time.monotonic()
            attempt = workspace.score(ws, iso, tag, candidate, conn=conn, func=name,
                                      strategy=f"regalloc-keyed-ab:{arm}", model="zero-model",
                                      run_id=run_id)
            timing["compile_seconds"] += time.monotonic() - started
            timing["compiles"] += 1
            dump = ws / f"{tag}_object_dump_normalized.s"
            text = dump.read_text() if attempt.compiled and dump.is_file() else None
            for path in ws.glob(tag + "*"):
                if path.is_file():
                    path.unlink(missing_ok=True)
            evidence = {"compiled": bool(attempt.compiled), "score": attempt.score,
                        "source_attribution": attempt.source_attribution,
                        "frontend": attempt.frontend, "compiler_recipe": attempt.compiler_recipe}
            return regalloc_search.Compiled(bool(attempt.compiled), workspace.repair_complete(attempt),
                                            text, attempt.diff or "", evidence)

        def key(candidate):
            started = time.monotonic()
            try:
                return ido_stages.optimizer_key(iso, ws, name, candidate)
            finally:
                timing["key_seconds"] += time.monotonic() - started
                timing["keys"] += 1

        def resolved(candidate, label, parent_source, same_as):
            record_model_proposal(conn, run_id=run_id, parent_attempt_id=None, prompt="",
                                  raw_response=candidate, status="duplicate", model="zero-model",
                                  kind="optimizer-key:regalloc", hypothesis=label)

        started = time.monotonic()
        base = compile_candidate(source, "baseline")
        target = ws / "target_object_dump_normalized.s"
        if not target.is_file():
            report["arms"][arm] = {"status": "no target dump"}
            continue
        try:
            outcome = regalloc_search.search(
                name, source, compile_candidate, target.read_text(), budget=budget, baseline=base,
                enable=True, key=key if use_key else None, key_cost=key_cost,
                resolved=resolved if use_key else None, rank_sites=rank)
        except Exception as exc:                          # one arm never stops the batch
            report["arms"][arm] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:400]}
            continue
        conn.commit()
        path = [r["label"] for r in outcome.log if not r.get("key_violation")]
        report["arms"][arm] = {
            **outcome.summary(), "status": "done",
            "compiles_with_baseline": outcome.compiles + 1,
            "spent": round(outcome.compiles + 1 + outcome.key_calls * key_cost, 2),
            "evaluated": len(path), "key_violations": sum(1 for r in outcome.log if r.get("key_violation")),
            "path": path, "depths": [r["depth"] for r in outcome.log if not r.get("key_violation")],
            "seconds": round(time.monotonic() - started, 1), **timing}
    conn.close()
    return report


def _one(args):
    row, out, budget, key_cost = args
    try:
        return run_function(row, Path(out), budget, key_cost)
    except Exception as exc:
        return {"function": row["name"], "status": "error", "error": f"{type(exc).__name__}: {exc}"[:400]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--budget", type=int, default=200)
    parser.add_argument("--key-cost", type=float, default=0.14)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rows = json.loads(args.cohort.read_text())["functions"]
    results = args.out / "results.jsonl"
    done = {json.loads(l)["function"] for l in results.read_text().splitlines()} if results.exists() else set()
    todo = [r for r in rows if r["name"] not in done and (not args.only or r["name"] in args.only)]
    todo = todo[:args.limit or None]
    with multiprocessing.get_context("spawn").Pool(args.workers) as pool, results.open("a") as stream:
        for report in pool.imap_unordered(_one, [(r, str(args.out), args.budget, args.key_cost) for r in todo]):
            stream.write(json.dumps(report) + "\n")
            stream.flush()
            arms = report.get("arms", {})
            print(json.dumps({"function": report["function"], "status": report["status"],
                              **{a: (v.get("exact"), v.get("spent")) for a, v in arms.items()}}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
