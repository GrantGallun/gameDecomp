"""Restored-holes coverage run (2026-09-25): do the generators lost in amend-20260919 cover the build-class holes?

Same harness, scheduler, depth, budget and logging as at-inline-20260924/round4.py, over the 22 functions whose
best residual is build-class holes only (group a) or those plus register allocation (group b). Arms differ only in
the candidate stream:
  control   solver.regalloc_mutations.variants (the Sept 23-24 stream)
  restored  stack_layout.variants and address_symbols.variants (with link segments) first, then the same stream
  pure_inline  the restored stream on code-v16 (freeze-v16.json: regalloc_mutations with pure_local_inlines)
  local_webs   the restored stream on code-v17 (freeze-v17.json: + operand_locals)
Then, in both arms, regalloc_search (budget REGALLOC_BUDGET, default 300) from the search's best non-exact node when
that node still has a register residual; its compiles are logged to the same attempts DB but are not world nodes.

Usage (WSL): ROUND_FREEZE=freeze.json ROUND_ARM=control|restored RESTART_STARTS=starts.json RESTART_NATIVE=...              RESTART_REPORT=report-<arm>.json python3 run.py [--jobs 4]
"""
import argparse
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import multiprocessing
from pathlib import Path
import shutil
import sqlite3
import sys
import time

OUT = Path(__file__).resolve().parents[1] / "population-transfer-20260922"
HERE = Path(__file__).resolve().parent
STARTS = Path(os.environ["RESTART_STARTS"])
BUDGET = int(os.environ.get("RESTART_BUDGET", "32"))
STAGE1 = json.loads((OUT / "freeze.json").read_text())
FROZEN = json.loads(Path(os.environ["ROUND_FREEZE"]).read_text())
CODE = Path(FROZEN["code_root"])
NATIVE = Path(os.environ["RESTART_NATIVE"])
REPO = Path.home() / "decomp/sbk1"
DB = NATIVE / "attempts.sqlite"


class DiskFloor(RuntimeError):
    pass


def _free_gb() -> float:
    return shutil.disk_usage("/mnt/c").free / 1e9


def _code_ok() -> bool:
    return all(hashlib.sha256((CODE / p).read_bytes()).hexdigest() == h for p, h in FROZEN["files"].items())


def _fold(name: str) -> int:
    return int(hashlib.sha256(name.encode()).hexdigest(), 16) % 2


def _setup_db():
    if DB.exists():
        return
    db = sqlite3.connect(DB)
    db.execute("pragma journal_mode=wal")
    db.executescript((CODE / "kb/schema.sql").read_text())
    upstream = sqlite3.connect(f"file:{STAGE1['kb']}?mode=ro", uri=True)
    for table in ("tus", "functions"):
        cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
        db.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                       upstream.execute(f"SELECT {','.join(cols)} FROM {table}"))
    db.commit()


def _ancestry_families(nodes: dict, identity: str) -> list[str]:
    families = []
    while identity and identity != "root":
        families.append(nodes[identity]["family"])
        identity = nodes[identity]["parent"]
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
    row_path = NATIVE / "rows" / f"{name}--{arm}.json"
    if row_path.exists():
        return json.loads(row_path.read_text())
    if _free_gb() < FROZEN["min_free_gb_on_c"]:
        raise DiskFloor(f"C: free {_free_gb():.1f} GB")
    out = NATIVE / "worlds" / f"{name}--{arm}.world.json"
    started = time.monotonic()
    db = sqlite3.connect(DB, timeout=300)
    repo = isolate(REPO, NATIVE / "ws" / name / arm, name)
    ws = repo / "nonmatchings" / name
    receipts = []
    prefer = tuple(FROZEN["prefer_by_fold"][str(_fold(name))]["prefer"]) if arm == "prior" else ()

    def call(candidate, label, parent):
        if _free_gb() < FROZEN["min_free_gb_on_c"]:
            raise DiskFloor(f"C: free {_free_gb():.1f} GB")
        verdict = compile_logged(ws, repo, name, candidate, conn=db,
            strategy=f"population-transfer:{arm}:{label}", run_id=f"population-transfer:{name}:{arm}",
            parent_attempt_id=parent, action=label, model="deterministic-repair-search",
            prompt="Frozen best compiling source, target assembly and compiler feedback only.",
            extra={"training_eligible": False, "arm": arm, "prefer": list(prefer)})
        receipts.append({"receipt_id": verdict["receipt_id"], "parent_receipt_id": parent,
                         "source_sha256": digest(candidate), "exact": verdict["exact"],
                         "error": verdict.get("error")})
        return verdict

    segments = None
    if arm != "control":
        import yaml
        from solver import address_symbols, stack_layout
        segments = address_symbols.segments_from(yaml.safe_load((repo / "snowboardkids.yaml").read_text()))

    def variants(parent_source, diff, evidence=None):
        if arm != "control":
            yield from stack_layout.variants(parent_source, name, diff)
            yield from address_symbols.variants(parent_source, name, diff, segments=segments)
        yield from regalloc_mutations.variants(parent_source, name, diff, evidence=evidence)

    row = {"function": name, "arm": arm, "insn_count": item["insn_count"], "fold": _fold(name),
           "prefer": list(prefer), "source_attempt_id": item["source_attempt_id"]}
    if not (ws / "target.o").exists():
        row.update(status="no-target-object", compiles=0, exact=False)
    else:
        context = {"task": name, "initial_sha256": item["source_sha256"], "partition": "evaluation",
                   "target_sha256": hashlib.sha256((ws / "target.o").read_bytes()).hexdigest(),
                   "compiler_sha256": compiler_identity(repo, ws),
                   "generator_sha256": digest({"files": FROZEN["files"], "arm": arm, "prefer": list(prefer)}),
                   "training_eligible": False, "assistance": "population-transfer"}
        environment = Online(source, call, variants, context, max_depth=FROZEN["max_depth"],
                             checkpoint=lambda w: save_world(w, out))
        policy = Policy(**FROZEN["scheduler"])
        result = run(environment, policy, BUDGET)
        if any(r["error"] for r in receipts):
            raise RuntimeError("compiler infrastructure error; row not recorded")
        if result["complete"]:
            assert result == run(Replay(environment.world), policy, BUDGET)
        assert compiler_identity(repo, ws) == context["compiler_sha256"]
        nodes = {n["id"]: n for n in environment.world["nodes"]}
        best, root = nodes[result["best_id"]], nodes["root"]
        row.update(status="ok", exact=result["exact"], compiles=result["compiles"], stop=result["stop"],
                   baseline_compiled=root["verdict"]["compiled"], baseline_exact=root["verdict"]["exact"],
                   baseline_score=result["baseline_score"], best_score=result["best_score"],
                   best_id=result["best_id"], best_source_sha256=best["source_sha256"],
                   best_path_families=_ancestry_families(nodes, result["best_id"]),
                   owner_calls=sum(n["family"].startswith("owner:") for n in nodes.values()),
                   receipts=receipts, world=str(out))
        if result["exact"]:
            row["exact_source"] = best["source"]
            row["exact_receipt_id"] = best["verdict"]["receipt_id"]
        elif best["verdict"]["compiled"] and int(os.environ.get("REGALLOC_BUDGET", "300")) > 0:
            row["regalloc"] = _regalloc(ws, repo, name, arm, best, db)
            if row["regalloc"].get("exact"):
                row["exact_source"] = row["regalloc"]["source"]
                row["exact_receipt_id"] = row["regalloc"]["receipt_id"]
    row["seconds"] = round(time.monotonic() - started, 2)
    row_path.parent.mkdir(exist_ok=True)
    temp = row_path.with_suffix(".tmp")
    temp.write_text(json.dumps(row, indent=1))
    temp.replace(row_path)                       # a disk-full mid-write can no longer leave a torn row
    return row


def _regalloc(ws, repo, name, arm, best, db) -> dict:
    """regalloc_search from the search's best node, compiles logged to the experiment DB (as agentrepair does)."""
    from solver import regalloc_search, regalloc_signature, workspace
    from eval.search_replay import digest
    target = ws / "target_object_dump_normalized.s"
    if not target.is_file():
        return {"status": "no-target-dump"}
    last = {}

    def compile_candidate(candidate, label):
        tag = f"{name}_regalloc_{time.time_ns()}"
        attempt = workspace.score(ws, repo, tag, candidate, conn=db, func=name,
                                  strategy=f"restored-holes:{arm}:regalloc_search:{label}",
                                  run_id=f"restored-holes:{name}:{arm}", parent_attempt_id=best["verdict"]["receipt_id"],
                                  model="deterministic-repair-search")
        dump = ws / f"{tag}_object_dump_normalized.s"
        text = dump.read_text() if attempt.compiled and dump.is_file() else None
        for path in ws.glob(tag + "*"):
            if path.is_file():
                path.unlink(missing_ok=True)
        exact = bool(attempt.exact and (attempt.frontend or {}).get("passed"))
        if exact:
            last[digest(candidate)] = attempt.receipt_id
        return regalloc_search.Compiled(bool(attempt.compiled), exact, text, attempt.diff or "")

    base = compile_candidate(best["source"], "baseline")
    report = regalloc_signature.compare(target.read_text(), base.dump) if base.compiled and base.dump else None
    if report is None or report.gradient[1] == 0:          # no register-differing instructions
        return {"status": "no-register-residual", "compiles": 1}
    budget = int(os.environ.get("REGALLOC_BUDGET", "300"))
    out = regalloc_search.search(name, best["source"], compile_candidate, target.read_text(), budget=budget,
                                 baseline=base, enable=True)
    row = {"status": "ok", "exact": out.exact, "best_label": out.best_label, "compiles": out.compiles + 1,
           "baseline_gradient": list(out.baseline_gradient or ()), "best_gradient": list(out.best_gradient or ())}
    if out.exact:
        row.update(source=out.best_source, receipt_id=last.get(digest(out.best_source)))
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
        prompt="Independent recompilation of a search-reported exact source.", extra={"training_eligible": False})
    return {"function": name, "source_sha256": sha, "exact": verdict["exact"], "receipt_id": verdict["receipt_id"],
            "parent_receipt_id": parent}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=STAGE1["nproc"])
    ap.add_argument("--functions", default="")
    args = ap.parse_args()
    sys.path.insert(0, str(CODE))
    assert _code_ok(), "frozen code changed"
    # Starting points: each function's best forward-derived candidate (derive -> search composition).
    population = json.loads(STARTS.read_text())
    if args.functions:
        population = [p for p in population if p["function"] in set(args.functions.split(","))]
    NATIVE.mkdir(parents=True, exist_ok=True)
    _setup_db()
    started = time.monotonic()
    tasks = [(item, os.environ["ROUND_ARM"]) for item in population]
    rows, stopped = [], None
    with ProcessPoolExecutor(max_workers=args.jobs, mp_context=multiprocessing.get_context("spawn")) as pool:
        futures = {pool.submit(run_arm, item, arm): (item["function"], arm) for item, arm in tasks}
        for future in as_completed(futures):
            name, arm = futures[future]
            try:
                row = future.result()
            except DiskFloor as exc:
                stopped = str(exc)
                for f in futures:
                    f.cancel()
                continue
            except Exception as exc:                        # noqa: BLE001
                row = {"function": name, "arm": arm, "status": "worker-raised", "error": f"{type(exc).__name__}: {exc}"}
            rows.append(row)
            print(json.dumps({"n": len(rows), "of": len(tasks), "function": name, "arm": arm,
                              "status": row.get("status"), "exact": row.get("exact"), "compiles": row.get("compiles"),
                              "base": row.get("baseline_score"), "best": row.get("best_score")}), flush=True)
    if stopped:
        print(json.dumps({"stopped": stopped, "rows": len(rows)}), flush=True)
        raise SystemExit(2)
    assert _code_ok(), "frozen code changed during the run"
    confirmations, exacts = [], {}
    for row in rows:
        if row.get("exact") and not row.get("baseline_exact"):
            exacts.setdefault((row["function"], row["best_source_sha256"]), row)
    for (name, _sha), row in sorted(exacts.items()):
        confirmations.append(confirm(name, row["exact_source"], row["exact_receipt_id"]))
        print(json.dumps({"confirm": name, "exact": confirmations[-1]["exact"]}), flush=True)
    report = {"kind": "restored-holes-coverage",
              "freeze_sha256": hashlib.sha256(Path(os.environ["ROUND_FREEZE"]).read_bytes()).hexdigest(),
              "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "functions": len(population), "seconds": round(time.monotonic() - started, 1),
              "rows": sorted(rows, key=lambda r: (r["function"], r["arm"])), "confirmations": confirmations,
              "attempts_logged": sqlite3.connect(DB).execute("select count(*) from attempts").fetchone()[0]}
    Path(os.environ["RESTART_REPORT"]).write_text(json.dumps(report, indent=1))
    print(json.dumps({"done": True, "seconds": report["seconds"], "attempts": report["attempts_logged"],
                      "confirmations": [(c["function"], c["exact"]) for c in confirmations]}), flush=True)


if __name__ == "__main__":
    main()
