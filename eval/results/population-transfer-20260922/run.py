"""Paired control/expanded search over the frozen population, then independent exact confirmation.

Per function and arm: a private isolated workspace, the frozen best source as root, the Codex
depth-1.18754 scheduler, a 32-call ceiling including the baseline, every compile logged to one
private attempts DB. The expanded arm also records, from the root's own diff and with no compile,
how many candidates each family proposes -- the firing census. A family that proposes nothing on
the whole population is a finding to explain, not a null.

Usage (WSL): python3 run.py [--jobs 4] [--limit N] [--functions a,b]
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import multiprocessing
from pathlib import Path
import sqlite3
import sys
import time

OUT = Path(__file__).resolve().parent
FROZEN = json.loads((OUT / "freeze.json").read_text())
CODE = Path(FROZEN["code_root"])
NATIVE = Path.home() / "decomp/experiments/population-transfer-20260922"
REPO = Path.home() / "decomp/sbk1"
DB = NATIVE / "attempts.sqlite"
NEW = set(FROZEN["new_families"])


def _code_ok() -> bool:
    return all(hashlib.sha256((CODE / p).read_bytes()).hexdigest() == h for p, h in FROZEN["files"].items())


def _setup_db():
    sys.path.insert(0, str(CODE))
    if DB.exists():
        return
    db = sqlite3.connect(DB)
    db.execute("pragma journal_mode=wal")
    db.executescript((CODE / "kb/schema.sql").read_text())
    upstream = sqlite3.connect(f"file:{FROZEN['kb']}?mode=ro", uri=True)
    for table in ("tus", "functions"):          # compiler_recipe reads the function's own recipe
        cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
        db.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                       upstream.execute(f"SELECT {','.join(cols)} FROM {table}"))
    db.commit()


def _ancestry_families(nodes: dict, identity: str) -> list[str]:
    families = []
    while identity and identity != "root":
        node = nodes[identity]
        families.append(node["family"])
        identity = node["parent"]
    return families[::-1]


def run_arm(item: dict, arm: str) -> dict:
    sys.path.insert(0, str(CODE))
    sys.path.append(str(CODE / "eval/results/dream-search-20260922"))
    from pilot import compiler_identity
    from eval.campaign_workers import isolate
    from eval.search_evolution import compile_logged
    from eval.search_replay import Policy, Replay, digest, run, save_world
    from eval.search_scheduler import Online
    from solver import regalloc_mutations

    name, source = item["function"], item["source"]
    assert digest(source) == item["source_sha256"]
    out = NATIVE / "worlds" / f"{name}--{arm}.world.json"
    if (NATIVE / "rows" / f"{name}--{arm}.json").exists():
        return json.loads((NATIVE / "rows" / f"{name}--{arm}.json").read_text())
    started = time.monotonic()
    db = sqlite3.connect(DB, timeout=300)
    repo = isolate(REPO, NATIVE / "ws" / name / arm, name)
    ws = repo / "nonmatchings" / name
    receipts = []

    def call(candidate, label, parent):
        verdict = compile_logged(ws, repo, name, candidate, conn=db,
            strategy=f"population-transfer:{arm}:{label}", run_id=f"population-transfer:{name}:{arm}",
            parent_attempt_id=parent, action=label, model="deterministic-repair-search",
            prompt="Frozen best compiling source, target assembly and compiler feedback only.",
            extra={"training_eligible": False, "arm": arm})
        receipts.append({"receipt_id": verdict["receipt_id"], "parent_receipt_id": parent,
                         "source_sha256": digest(candidate), "exact": verdict["exact"],
                         "error": verdict.get("error")})
        return verdict

    def variants(parent_source, diff):
        for label, family, child in regalloc_mutations.variants(parent_source, name, diff):
            if arm == "expanded" or family not in NEW:
                yield label, family, child

    row = {"function": name, "arm": arm, "insn_count": item["insn_count"],
           "source_attempt_id": item["source_attempt_id"], "source_score": item["source_score"]}
    if not (ws / "target.o").exists():
        row.update(status="no-target-object", compiles=0, exact=False)
    else:
        context = {"task": name, "initial_sha256": item["source_sha256"], "partition": "evaluation",
                   "target_sha256": hashlib.sha256((ws / "target.o").read_bytes()).hexdigest(),
                   "compiler_sha256": compiler_identity(repo, ws),
                   "generator_sha256": digest({"files": FROZEN["files"], "arm": arm}),
                   "training_eligible": False, "assistance": "population-transfer"}
        environment = Online(source, call, variants, context, max_depth=FROZEN["max_depth"],
                             checkpoint=lambda w: save_world(w, out))
        policy = Policy(**FROZEN["scheduler"])
        result = run(environment, policy, FROZEN["budget_per_arm"])
        if result["complete"] and not any(r["error"] for r in receipts):
            assert result == run(Replay(environment.world), policy, FROZEN["budget_per_arm"])
        assert compiler_identity(repo, ws) == context["compiler_sha256"]
        nodes = {n["id"]: n for n in environment.world["nodes"]}
        best = nodes[result["best_id"]]
        root = nodes["root"]
        row.update(status="ok", exact=result["exact"], compiles=result["compiles"], stop=result["stop"],
                   baseline_compiled=root["verdict"]["compiled"], baseline_exact=root["verdict"]["exact"],
                   baseline_score=result["baseline_score"], best_score=result["best_score"],
                   best_id=result["best_id"], best_source_sha256=best["source_sha256"],
                   best_path_families=_ancestry_families(nodes, result["best_id"]),
                   new_family_calls=sum(n["family"] in NEW for n in nodes.values()),
                   infrastructure_errors=sum(bool(r["error"]) for r in receipts),
                   receipts=receipts, world=str(out))
        if result["exact"]:
            row["exact_source"] = best["source"]
            row["exact_receipt_id"] = best["verdict"]["receipt_id"]
        if arm == "expanded" and root["verdict"]["compiled"] and not root["verdict"]["exact"]:
            census: dict[str, int] = {}
            for _label, family, _child in regalloc_mutations.variants(source, name, root["verdict"].get("diff", "")):
                census[family] = census.get(family, 0) + 1
            row["root_census"] = census
    row["seconds"] = round(time.monotonic() - started, 2)
    (NATIVE / "rows").mkdir(exist_ok=True)
    (NATIVE / "rows" / f"{name}--{arm}.json").write_text(json.dumps(row, indent=1))
    return row


def confirm(name: str, source: str, parent: int) -> dict:
    sys.path.insert(0, str(CODE))
    from eval.campaign_workers import isolate
    from eval.search_evolution import compile_logged
    from eval.search_replay import digest
    sha = digest(source)
    repo = isolate(REPO, NATIVE / "confirm" / f"{name}--{sha[:12]}", name)
    db = sqlite3.connect(DB, timeout=300)
    verdict = compile_logged(repo / "nonmatchings" / name, repo, name, source, conn=db,
        strategy="population-transfer:independent-confirmation", run_id=f"population-transfer:{name}:confirm",
        parent_attempt_id=parent, action="independent-confirmation", model="deterministic-repair-search",
        prompt="Independent recompilation of a search-reported exact source.",
        extra={"training_eligible": False})
    return {"function": name, "source_sha256": sha, "exact": verdict["exact"],
            "receipt_id": verdict["receipt_id"], "parent_receipt_id": parent,
            "verification": verdict.get("verification"), "frontend": verdict.get("frontend")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=FROZEN["nproc"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--functions", default="")
    args = ap.parse_args()
    assert _code_ok(), "frozen code changed"
    population = json.loads((OUT / "population.json").read_text())
    assert hashlib.sha256((OUT / "population.json").read_bytes()).hexdigest() == FROZEN["population_sha256"]
    if args.functions:
        wanted = set(args.functions.split(","))
        population = [p for p in population if p["function"] in wanted]
    if args.limit:
        population = population[:args.limit]
    NATIVE.mkdir(parents=True, exist_ok=True)
    _setup_db()
    started = time.monotonic()
    tasks = [(item, arm) for item in population for arm in ("control", "expanded")]
    rows = []
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=args.jobs, mp_context=ctx) as pool:
        futures = {pool.submit(run_arm, item, arm): (item["function"], arm) for item, arm in tasks}
        for future in as_completed(futures):
            name, arm = futures[future]
            try:
                row = future.result()
            except Exception as exc:                        # a dead worker must not drop the function
                row = {"function": name, "arm": arm, "status": "worker-raised",
                       "error": f"{type(exc).__name__}: {exc}"}
            rows.append(row)
            print(json.dumps({"n": len(rows), "of": len(tasks), "function": name, "arm": arm,
                              "status": row.get("status"), "exact": row.get("exact"),
                              "compiles": row.get("compiles"), "base": row.get("baseline_score"),
                              "best": row.get("best_score")}), flush=True)
    assert _code_ok(), "frozen code changed during the run"
    confirmations = []
    exacts = {}
    for row in rows:
        if row.get("exact") and not row.get("baseline_exact"):
            exacts.setdefault((row["function"], row["best_source_sha256"]), row)
    for (name, _sha), row in sorted(exacts.items()):
        confirmations.append(confirm(name, row["exact_source"], row["exact_receipt_id"]))
        print(json.dumps({"confirm": name, "exact": confirmations[-1]["exact"]}), flush=True)
    report = {"kind": "population-transfer", "freeze_sha256": hashlib.sha256((OUT / "freeze.json").read_bytes()).hexdigest(),
              "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "functions": len(population), "seconds": round(time.monotonic() - started, 1),
              "rows": sorted(rows, key=lambda r: (r["function"], r["arm"])), "confirmations": confirmations,
              "attempts_logged": sqlite3.connect(DB).execute("select count(*) from attempts").fetchone()[0]}
    name = "report.json" if not (args.limit or args.functions) else "report-partial.json"
    (OUT / name).write_text(json.dumps(report, indent=1))
    print(json.dumps({"done": True, "seconds": report["seconds"], "attempts": report["attempts_logged"],
                      "confirmations": [(c["function"], c["exact"]) for c in confirmations]}), flush=True)


if __name__ == "__main__":
    main()
