"""Equal-budget repair-family ablation on frozen generated inputs."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

SHARED = Path(__file__).resolve().parent
FROZEN = json.loads((SHARED / "freeze-v3.json").read_text())
CODE = Path(FROZEN["code_root"])
sys.path.insert(0, str(CODE))
sys.path.append(str(CODE / "eval/results/dream-search-20260922"))
from pilot import compiler_identity
from eval.campaign_workers import isolate
from eval.budget_ledger import Caps, Ledger
from eval.search_evolution import compile_logged
from eval.search_replay import Policy, Replay, digest, run, save_world
from eval.search_scheduler import Online
from solver import regalloc_mutations

OUT = SHARED / "paired-v3"
NATIVE = Path.home() / "decomp/experiments/register-storage-20260922/paired-v3"
REPO = Path.home() / "decomp/sbk1"
PRIOR = SHARED.parent / "search-evolution-20260922/diagnosis-v1"
NEW = {"parameter_reuse"}
KNOWN = ["Fvibup", "Fvibdown", "Fdistort", "loadMusicSequenceBank", "__MusIntProcessWobble", "__osDequeueThread"]
NEGATIVE = ["osCreateViManager", "updateRacePlayerAirborneLaunch", "updateRacePlayerMode06TerrainFall"]


class Stop(BaseException):
    pass


def write(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, allow_nan=False))
    temp.replace(path)


def main():
    OUT.mkdir(exist_ok=False)
    NATIVE.mkdir(parents=True, exist_ok=False)
    panel = json.loads((SHARED / "panel-v2.json").read_text())
    sources, cases = {}, []
    for name in ["osGetThreadPri", *KNOWN, *NEGATIVE]:
        path = (SHARED / "paired-v1/followup--osGetThreadPri--control.world.json") if name == "osGetThreadPri" else next(PRIOR.glob(f"*--{name}--breadth.world.json"))
        world = json.loads(path.read_text())["world"]
        sources[name] = world["nodes"][0]["source"]
        cases.append({"function": name, "phase": "motivation" if name == "osGetThreadPri" else "retention" if name in KNOWN else "negative",
                      "assistance_tier": world["context"]["assistance"]})
    for row in panel["rows"]:
        if row["status"] != "generated":
            continue
        source = (SHARED / row["source"]).read_text()
        assert digest(source) == row["source_sha256"]
        sources[row["function"]] = source
        cases.append({"function": row["function"], "phase": "followup", "assistance_tier": row["assistance_tier"]})
    caps = Caps(model_calls=0, compiles=0, evaluation_compiles=1280, evaluation_calls=0,
                seconds=1200, train_steps=0, tokens=0, panel_functions=18, per_task_actions=32)
    ledger = Ledger.open(NATIVE / "budget.jsonl", caps)
    ledger.reserve_evaluation("paired", compiles=1280)
    policy = Policy(**FROZEN["scheduler"])
    report = {"kind": "frozen-storage-repair-ablation", "training_eligible": False,
        "production_mutated": False, "model_calls": 0, "code_root": str(CODE), "fixed_files": FROZEN["files"],
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "panel_sha256": hashlib.sha256((SHARED / "panel-v2.json").read_bytes()).hexdigest(),
        "source_sha256": {k: digest(v) for k,v in sources.items()}, "cases": cases,
        "unavailable": [r for r in panel["rows"] if r["status"] != "generated"],
        "policy": asdict(policy), "budget": 32, "caps": caps.as_dict(),
        "database": str(NATIVE / "attempts.sqlite"), "runs": [], "confirmations": [], "complete": False}
    write(OUT / "preregistration.json", report)
    db = sqlite3.connect(NATIVE / "attempts.sqlite")
    db.executescript((CODE / "kb/schema.sql").read_text())
    upstream = sqlite3.connect(f"file:{Path.home() / 'decomp/kb-sbk1.sqlite'}?mode=ro", uri=True)
    for table in ("tus", "functions"):
        cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
        db.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                       upstream.execute(f"SELECT {','.join(cols)} FROM {table}"))
    db.commit()
    start, count, confirmations = time.monotonic(), 0, {}
    before = {r[0] for r in upstream.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    def check():
        if time.monotonic() - start > caps.seconds or (OUT / "STOP").exists():
            raise Stop("time/stop ceiling")
        if any(hashlib.sha256((CODE / p).read_bytes()).hexdigest() != sha for p,sha in FROZEN["files"].items()):
            raise Stop("frozen code changed")
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != report["driver_sha256"]:
            raise Stop("driver changed")
    def save():
        report["ledger"] = ledger.snapshot()
        write(OUT / "report.json", report)
    def compiler(case, arm):
        name = case["function"]
        repo = isolate(REPO, NATIVE / name / arm, name)
        ws, receipts = repo / "nonmatchings" / name, []
        def call(source, label, parent):
            nonlocal count
            check()
            ledger.spend_evaluation("paired", op=f"compile-{count+1}", compiles=1)
            count += 1
            verdict = compile_logged(ws, repo, name, source, conn=db,
                strategy=f"storage-repair-v3:{arm}:{label}", run_id=f"storage-repair-v3:{name}:{arm}",
                parent_attempt_id=parent, action=label, model="deterministic-repair-search",
                prompt="Frozen generated draft, target assembly and compiler feedback only.",
                extra={"training_eligible": False, "assistance_tier": case["assistance_tier"],
                       "fixed_files_sha256": digest(FROZEN["files"])})
            if verdict.get("error"):
                raise Stop("compiler infrastructure error")
            receipts.append({"receipt_id": verdict["receipt_id"], "parent_receipt_id": parent,
                             "source_sha256": digest(source), "exact": verdict["exact"]})
            return verdict
        return repo, ws, call, receipts
    for case in cases:
        name = case["function"]
        for arm in ("control", "expanded"):
            repo, ws, callback, receipts = compiler(case, arm)
            def variants(source, diff):
                for label, family, child in regalloc_mutations.variants(source, name, diff):
                    if arm == "expanded" or family not in NEW:
                        yield label, family, child
            context = {"task": name, "initial_sha256": digest(sources[name]), "partition": "evaluation",
                "target_sha256": hashlib.sha256((ws / "target.o").read_bytes()).hexdigest(),
                "compiler_sha256": compiler_identity(repo, ws),
                "generator_sha256": digest({"files": FROZEN["files"], "arm": arm}),
                "training_eligible": False, "assistance": case["assistance_tier"]}
            path = OUT / f"{case['phase']}--{name}--{arm}.world.json"
            environment = Online(sources[name], callback, variants, context, checkpoint=lambda w: save_world(w,path))
            result = run(environment, policy, report["budget"])
            assert result == run(Replay(environment.world), policy, report["budget"])
            assert compiler_identity(repo, ws) == context["compiler_sha256"]
            best = next(n for n in environment.world["nodes"] if n["id"] == result["best_id"])
            (OUT / f"{case['phase']}--{name}--{arm}.c").write_text(best["source"])
            if result["exact"]:
                confirmations[(name, best["source_sha256"])] = (case, best["source"], best["verdict"]["receipt_id"])
            report["runs"].append({**case, "arm": arm, **result, "world": path.name,
                "source_sha256": best["source_sha256"], "receipts": receipts,
                "new_family_calls": sum(n["family"] in NEW for n in environment.world["nodes"])})
            save()
            print(json.dumps({"function": name, "phase": case["phase"], "arm": arm,
                "exact": result["exact"], "compiles": result["compiles"], "score": result["best_score"]}), flush=True)
    for (name, sha), (case, source, parent) in confirmations.items():
        _, _, callback, _ = compiler(case, "confirm-" + sha[:12])
        verdict = callback(source, "independent-confirmation", parent)
        assert verdict["exact"]
        path = OUT / f"confirmed--{name}--{sha[:12]}.json"
        write(path, verdict)
        report["confirmations"].append({"function": name, "source_sha256": sha,
            "receipt_id": verdict["receipt_id"], "parent_receipt_id": parent, "certificate": path.name})
        save()
    report["comparison"] = []
    for case in cases:
        parent, child = [r for r in report["runs"] if r["function"] == case["function"]]
        report["comparison"].append({**case, "gained": child["exact"] and not parent["exact"],
            "lost": parent["exact"] and not child["exact"], "control_exact": parent["exact"],
            "expanded_exact": child["exact"], "control_compiles": parent["compiles"], "expanded_compiles": child["compiles"]})
    report["known_losses"] = [r["function"] for r in report["comparison"] if r["function"] in KNOWN and not r["expanded_exact"]]
    report["followup_gains"] = [r["function"] for r in report["comparison"] if r["phase"] == "followup" and r["gained"]]
    report["losses"] = [r["function"] for r in report["comparison"] if r["lost"]]
    check()
    assert before == {r[0] for r in upstream.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    report["total_compiles"] = count
    assert count == db.execute("SELECT count(*) FROM attempts").fetchone()[0]
    assert count == sum(r["compiles"] for r in report["runs"]) + len(report["confirmations"])
    ledger.release("paired")
    report.update(complete=True, seconds=time.monotonic()-start)
    save()
    print(json.dumps({"complete": True, "compiles": count, "followup_gains": report["followup_gains"],
                      "known_losses": report["known_losses"], "losses": report["losses"]}), flush=True)


if __name__ == "__main__":
    main()
