"""Postmortem, frozen repair candidate and fresh compiler checks.

The prior evaluation is now explicitly used for development diagnosis. It is not
relabelled training data or claimed as a fresh held-out success. New follow-up
tasks remain separate from the proposal and development-regression selector.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "eval/results/dream-search-20260922"))
from pilot import compiler_identity
from eval import search_evolution as evolution
from eval.budget_ledger import BudgetExceeded, Caps, Ledger
from eval.campaign_workers import isolate
from eval.search_replay import Policy, Replay, digest, load_world, merge_worlds, run, save_world
from eval.search_scheduler import Online
from solver import regalloc_mutations, regalloc_search

SHARED = Path("/mnt/c/Code/gameDecomp/eval/results/search-evolution-20260922")
PRIOR = SHARED / "pilot-v2"
OUT = SHARED / "diagnosis-v1"
NATIVE = Path.home() / "decomp/experiments/search-evolution-20260922/diagnosis-v1"
REPO = Path.home() / "decomp/sbk1"
BUDGET = 32
RESEARCH = ["Fvibup", "Fdistort", "loadMusicSequenceBank", "releaseMenuAssetHandles"]
DIAGNOSTIC = RESEARCH + ["Fvibdown", "__MusIntProcessWobble", "allocTranslationOnlyFixedMatrix",
                         "updateRacePlayerLeanAngle", "__MusIntProcessVibrato"]
FOLLOWUP = ["__osDequeueThread", "osCreateViManager", "updateRacePlayerAirborneLaunch", "updateRacePlayerMode06TerrainFall"]


class Stopped(BaseException):
    pass


def write(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, allow_nan=False))
    temp.replace(path)


def merged(name):
    return merge_worlds([load_world(p) for p in sorted(PRIOR.glob(f"*--{name}--*.world.json"))])


def main():
    OUT.mkdir(exist_ok=False)
    NATIVE.mkdir(parents=True, exist_ok=False)
    parent = Policy("breadth", "breadth")
    old = json.loads((PRIOR / "report.json").read_text())
    parent_research = [load_world(PRIOR / f"r1-research--{name}--breadth.world.json") for name in RESEARCH]
    proposals = evolution.propose(parent_research, parent)
    selection = evolution.select([merged(name) for name in RESEARCH], parent, budget=BUDGET, proposals=proposals,
        regressions=[{"kind": "development-regression", "world": merged("__MusIntProcessWobble")}])
    assert selection["eligible"]
    candidate = Policy(**selection["candidate"])
    # The replay decides; no hardcoded function-specific policy or winning value.
    write(OUT / "selection.json", {"proposal_inputs": [digest(w) for w in parent_research],
          "proposals": [asdict(p) for p in proposals], "selection": selection})
    selection_sha = hashlib.sha256((OUT / "selection.json").read_bytes()).hexdigest()
    diagnosis = {"kind": "retrospective-development-analysis", "training_eligible": False,
        "selection_sha256": selection_sha, "policy": asdict(candidate), "replays": []}
    for name in DIAGNOSTIC:
        world = merged(name)
        results = {p.name: run(Replay(world), p, BUDGET) for p in [parent, Policy("greedy", "greedy"), candidate]}
        diagnosis["replays"].append({"function": name, "world_sha256": digest(world), "results": results})
    greedy_wobble = load_world(PRIOR / "r1-candidate--__MusIntProcessWobble--greedy.world.json")
    diagnosis["wobble"] = {"compiles": len(greedy_wobble["nodes"]),
        "unique_sources": len({n["source_sha256"] for n in greedy_wobble["nodes"]}),
        "first_gain": round(greedy_wobble["nodes"][1]["verdict"]["score"] - greedy_wobble["nodes"][0]["verdict"]["score"], 6),
        "root_children_tried": sum(n["parent"] == "root" for n in greedy_wobble["nodes"]),
        "exact_root_child": "root/10"}
    write(OUT / "diagnosis.json", diagnosis)

    dev = json.loads((ROOT / "eval/results/dev-set-20260921/dev-set.json").read_text())
    entries = {r["function"]: r for r in dev["entries"]}
    sources = {}
    for name in DIAGNOSTIC + FOLLOWUP:
        path = ROOT / "eval/results/repair-mechanisms-20260922/inputs" / name / "initial.c"
        if not path.exists():
            path = ROOT / "eval/results/dev-set-20260921/sources" / f"{name}.c"
            assert entries[name]["assistance"]["tier"] != "reference-source-assisted"
        sources[name] = path.read_text()
        assert digest(sources[name]) == old["source_sha256"][name]
    fixed_files = sorted((ROOT / "solver").glob("*.py")) + [Path(__file__), ROOT / "eval/search_evolution.py",
        ROOT / "eval/search_replay.py", ROOT / "eval/search_scheduler.py", ROOT / "eval/budget_ledger.py",
        ROOT / "eval/campaign_workers.py", ROOT / "eval/tool_agent_run.py", ROOT / "eval/results/dream-search-20260922/pilot.py"]
    fixed = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in fixed_files}
    caps = Caps(model_calls=0, compiles=0, evaluation_compiles=1024, evaluation_calls=0,
                seconds=1200, train_steps=0, tokens=0, panel_functions=13, per_task_actions=BUDGET)
    ledger = Ledger.open(NATIVE / "budget.jsonl", caps)
    ledger.reserve_evaluation("evaluation", compiles=1024)
    report = {"kind": "retrospective-repair-plus-separate-development-followup", "training_eligible": False,
        "model_calls": 0, "production_mutated": False, "code_root": str(ROOT), "fixed_files": fixed,
        "selection_sha256": selection_sha, "candidate": asdict(candidate), "parent": asdict(parent),
        "diagnostic": DIAGNOSTIC, "followup": FOLLOWUP, "budget": BUDGET, "caps": caps.as_dict(),
        "source_sha256": {k: digest(v) for k, v in sources.items()}, "assistance": old["assistance"],
        "database": str(NATIVE / "attempts.sqlite"), "runs": [], "confirmations": [],
        "claim_scope": "diagnostic cases informed revision; followup unused in pilot-v2 but previously exposed development",
        "activation": "none; original S0 and original rejection remain unchanged"}
    write(OUT / "preregistration.json", report)
    conn = sqlite3.connect(NATIVE / "attempts.sqlite")
    conn.executescript((ROOT / "kb/schema.sql").read_text())
    upstream = sqlite3.connect(f"file:{Path.home() / 'decomp/kb-sbk1.sqlite'}?mode=ro", uri=True)
    for table in ("tus", "functions"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        conn.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                         upstream.execute(f"SELECT {','.join(cols)} FROM {table}"))
    conn.commit()
    before = {r[0] for r in upstream.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    started, count = time.monotonic(), 0
    confirmations = {}

    def check():
        if time.monotonic() - started > caps.seconds or (OUT / "STOP").exists():
            raise Stopped("time ceiling or stop request")
        if fixed != {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in fixed_files}:
            raise Stopped("frozen code changed")
        if hashlib.sha256((OUT / "selection.json").read_bytes()).hexdigest() != selection_sha:
            raise Stopped("frozen selection changed")

    def save():
        report["ledger"] = ledger.snapshot()
        write(OUT / "report.json", report)

    def compiler(name, phase, arm):
        repo = isolate(REPO, NATIVE / phase / name / arm, name)
        ws = repo / "nonmatchings" / name
        receipts = []
        def call(source, label, parent_receipt):
            nonlocal count
            check()
            try:
                ledger.spend_evaluation("evaluation", op=f"compile-{count + 1}", compiles=1)
            except BudgetExceeded as exc:
                raise Stopped(str(exc)) from exc
            count += 1
            verdict = evolution.compile_logged(ws, repo, name, source, conn=conn,
                strategy=f"search-diagnosis:{phase}:{arm}:{label}",
                run_id=f"search-diagnosis:{phase}:{name}:{arm}", parent_attempt_id=parent_receipt,
                action=label, model="deterministic-search", prompt="Generated draft, encoded target and compiler feedback only.",
                extra={"training_eligible": False, "assistance_tier": old["assistance"][name],
                       "fixed_files_sha256": digest(fixed)})
            dump = ws / f"{name}_object_dump_normalized.s"
            verdict["dump"] = dump.read_text() if verdict["compiled"] and dump.exists() else None
            receipts.append({"receipt_id": verdict["receipt_id"], "parent_receipt_id": parent_receipt,
                             "source_sha256": digest(source), "exact": verdict["exact"], "error": verdict.get("error")})
            return verdict
        return repo, ws, call, receipts

    def scheduled(name, phase, policy):
        repo, ws, call, receipts = compiler(name, phase, policy.name)
        context = {"task": name, "initial_sha256": digest(sources[name]), "partition": "evaluation",
            "target_sha256": hashlib.sha256((ws / "target.o").read_bytes()).hexdigest(),
            "compiler_sha256": compiler_identity(repo, ws), "generator_sha256": digest(fixed),
            "training_eligible": False, "assistance": old["assistance"][name]}
        path = OUT / f"{phase}--{name}--{policy.name}.world.json"
        env = Online(sources[name], call, lambda source, diff: regalloc_mutations.variants(source, name, diff),
                     context, checkpoint=lambda w: save_world(w, path))
        result = run(env, policy, BUDGET)
        assert result == run(Replay(env.world), policy, BUDGET)
        assert compiler_identity(repo, ws) == context["compiler_sha256"]
        if any(n["verdict"].get("error") for n in env.world["nodes"]):
            raise Stopped("compiler infrastructure error")
        best = next(n for n in env.world["nodes"] if n["id"] == result["best_id"])
        if result["exact"]:
            confirmations[(name, best["source_sha256"])] = (best["source"], best["verdict"]["receipt_id"])
        row = {"function": name, "phase": phase, "policy": asdict(policy), **result,
               "source_sha256": best["source_sha256"], "world": path.name, "receipts": receipts}
        report["runs"].append(row)
        save()
        print(json.dumps({"function": name, "phase": phase, "policy": policy.name,
                          "exact": result["exact"], "compiles": result["compiles"]}), flush=True)
        return env.world, row

    def beam(name):
        repo, ws, call, receipts = compiler(name, "followup", "beam")
        original = regalloc_mutations.variants
        parents, known = {}, {}
        def variants(source, function, diff="", prefer=()):
            for label, family, child in original(source, function, diff, prefer):
                parents[digest(child)] = known[digest(source)]
                yield label, family, child
        def callback(source, label):
            assert len(receipts) < BUDGET
            parent_receipt = parents.get(digest(source))
            assert label == "baseline" or parent_receipt is not None
            verdict = call(source, label, parent_receipt)
            known[digest(source)] = verdict["receipt_id"]
            if verdict.get("error"):
                raise Stopped("compiler infrastructure error")
            return regalloc_search.Compiled(verdict["compiled"], verdict["exact"], verdict["dump"], verdict["diff"])
        regalloc_mutations.variants = variants
        try:
            base = callback(sources[name], "baseline")
            result = regalloc_search.search(name, sources[name], callback,
                (ws / "target_object_dump_normalized.s").read_text(), budget=BUDGET - 1, beam=3, depth=4, baseline=base)
        finally:
            regalloc_mutations.variants = original
        if result.exact:
            confirmations[(name, digest(result.best_source))] = (result.best_source, known[digest(result.best_source)])
        row = {"function": name, "phase": "followup", "policy": {"name": "beam"}, "exact": result.exact,
               "compiles": len(receipts), "source_sha256": digest(result.best_source), "receipts": receipts}
        (OUT / f"followup--{name}--beam.c").write_text(result.best_source)
        report["runs"].append(row)
        save()
        return row

    diagnostic_parent, diagnostic_candidate = [], []
    for name in DIAGNOSTIC:
        before_world, _ = scheduled(name, "diagnostic", parent)
        after_world, _ = scheduled(name, "diagnostic", candidate)
        merge_worlds([before_world, after_world])
        diagnostic_parent.append(before_world)
        diagnostic_candidate.append(after_world)
        if name == "__MusIntProcessWobble":
            scheduled(name, "diagnostic", Policy("greedy", "greedy"))
    followup_parent, followup_candidate, anchors = [], [], []
    for name in FOLLOWUP:
        followup_parent.append(scheduled(name, "followup", parent)[0])
        followup_candidate.append(scheduled(name, "followup", candidate)[0])
        anchors.append(beam(name))
    decision = evolution.gate(followup_parent, followup_candidate, parent, candidate, budget=BUDGET,
        research_tasks=set(DIAGNOSTIC))
    known_exacts = {r["function"] for r in old["runs"] if r["exact"]}
    replicated = {r["function"] for r in report["runs"] if r["phase"] == "diagnostic"
                  and r["policy"]["name"] == candidate.name and r["exact"]}
    decision["diagnostic_known_losses"] = sorted(known_exacts - replicated)
    if decision["diagnostic_known_losses"]:
        decision.update(advance=False, reason="lost a previously known development exact")
    decision["beam_losses"] = [a["function"] for a, r in zip(anchors, decision["candidate_results"]) if a["exact"] and not r["exact"]]
    if decision["beam_losses"]:
        decision.update(advance=False, reason="lost existing beam exact")
    for (name, sha), (source, parent_receipt) in confirmations.items():
        _, _, call, _ = compiler(name, "confirm", sha[:12])
        verdict = call(source, "independent-confirmation", parent_receipt)
        assert verdict["exact"] and not verdict.get("error")
        path = OUT / f"confirmed--{name}--{sha[:12]}.json"
        write(path, verdict)
        report["confirmations"].append({"function": name, "source_sha256": sha, "exact": True,
            "receipt_id": verdict["receipt_id"], "parent_receipt_id": parent_receipt, "certificate": path.name})
        save()
    check()
    assert before == {r[0] for r in upstream.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    report["followup_gate"] = decision
    ledger.release("evaluation")
    report["total_compiles"] = conn.execute("SELECT count(*) FROM attempts").fetchone()[0]
    assert report["total_compiles"] == count == sum(r["compiles"] for r in report["runs"]) + len(report["confirmations"])
    report["complete"] = True
    report["seconds"] = time.monotonic() - started
    save()
    print(json.dumps({"complete": True, "compiles": count, "followup_gate": decision["reason"]}), flush=True)


if __name__ == "__main__":
    main()
