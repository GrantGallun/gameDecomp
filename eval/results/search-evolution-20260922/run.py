"""Two bounded lab rounds; a failed capability gate ends succession.

Run with the SBK1 Python in WSL. Uses generated development drafts only. The
panels are function-disjoint within this experiment, previously exposed, and NOT
family-disjoint holdouts. Policy proposals and selection are automatic; the
algorithm, budgets, panels and acceptance rules are developer-authored.
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
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
from eval import generation_manifest as gm, search_evolution as evolution
from eval.budget_ledger import BudgetExceeded, Caps, Ledger
from eval.campaign_workers import isolate
from eval.search_replay import Policy, Replay, digest, merge_worlds, run, save_world
from eval.search_scheduler import Online
from solver import regalloc_mutations, regalloc_search

OUT = Path(__file__).resolve().parent
REPO = Path.home() / "decomp/sbk1"
DEV = ROOT / "eval/results/dev-set-20260921"
REPAIRS = ROOT / "eval/results/repair-mechanisms-20260922"
BUDGET = 32
RESEARCH = ["Fvibup", "Fdistort", "loadMusicSequenceBank", "releaseMenuAssetHandles"]
ADDITIONAL_RESEARCH = ["FrandPan", "drawCharacterSelectSelectedCharacterTokens"]
PANELS = [["Fvibdown", "__MusIntProcessWobble", "allocTranslationOnlyFixedMatrix",
           "updateRacePlayerLeanAngle", "__MusIntProcessVibrato"],
          ["__osDequeueThread", "osCreateViManager", "updateRacePlayerAirborneLaunch", "updateRacePlayerMode06TerrainFall"]]


class ExperimentStopped(BaseException):
    """Stop immediately; the scheduler must not reinterpret control as a build refusal."""


def write(path, data):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-root", type=Path, default=OUT)
    args = parser.parse_args()
    if not args.run_id.replace("-", "").isalnum():
        parser.error("safe run ID required")
    output = args.output_root.resolve() / args.run_id
    native = Path.home() / "decomp/experiments/search-evolution-20260922" / args.run_id
    output.mkdir(exist_ok=False)
    native.mkdir(parents=True, exist_ok=False)
    conn = sqlite3.connect(native / "attempts.sqlite")
    conn.executescript((ROOT / "kb/schema.sql").read_text())
    main_db = Path.home() / "decomp/kb-sbk1.sqlite"
    upstream = sqlite3.connect(f"file:{main_db}?mode=ro", uri=True)
    for table in ("tus", "functions"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        conn.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                         upstream.execute(f"SELECT {','.join(cols)} FROM {table}"))
    before = {r[0] for r in upstream.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    conn.commit()
    entries = {r["function"]: r for r in json.loads((DEV / "dev-set.json").read_text())["entries"]}
    all_names = RESEARCH + ADDITIONAL_RESEARCH + sum(PANELS, [])
    assert len(set(all_names)) == len(all_names)
    sources, assistance = {}, {}
    for name in all_names:
        path = REPAIRS / "inputs" / name / "initial.c"
        if path.exists():
            sources[name] = path.read_text()
            assistance[name] = "project-header-assisted" if '#include "game/' in sources[name] else "source-independent"
        else:
            assert entries[name]["assistance"]["tier"] != "reference-source-assisted"
            sources[name] = (DEV / "sources" / f"{name}.c").read_text()
            assert digest(sources[name]) == entries[name]["sha256"]
            assistance[name] = entries[name]["assistance"]["tier"]
    fixed_files = sorted((ROOT / "solver").glob("*.py")) + [Path(__file__), Path(evolution.__file__),
        ROOT / "eval/search_replay.py", ROOT / "eval/search_scheduler.py", ROOT / "eval/budget_ledger.py",
        ROOT / "eval/campaign_workers.py", ROOT / "eval/tool_agent_run.py", ROOT / "eval/generation_manifest.py",
        ROOT / "eval/results/dream-search-20260922/pilot.py"]
    fixed = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in fixed_files}
    caps = Caps(model_calls=0, compiles=1600, evaluation_compiles=1120, evaluation_calls=0,
                seconds=1800, train_steps=0, tokens=0, panel_functions=9, per_task_actions=BUDGET)
    ledger = Ledger.open(native / "budget.jsonl", caps)
    ledger.reserve_evaluation("evaluation", compiles=1120)
    report = {"kind": "automatic-search-policy-development-succession", "run_id": args.run_id,
        "training_eligible": False, "model_calls": 0, "production_mutated": False,
        "rounds_declared": 2, "per_arm_compile_ceiling": BUDGET, "caps": caps.as_dict(),
        "research": RESEARCH, "additional_research": ADDITIONAL_RESEARCH, "evaluation_panels": PANELS,
        "source_sha256": {k: digest(v) for k, v in sources.items()}, "assistance": assistance,
        "fixed_files": fixed, "database": str(native / "attempts.sqlite"),
        "claim_scope": "previously exposed function-disjoint development; related vibrato siblings cross partitions",
        "code_root": str(ROOT),
        "gate_rule": "one new exact and zero lost exacts versus parent; retain prior accepted matches; no losses versus beam; fresh confirmation required",
        "runs": [], "rounds": [], "confirmations": [], "active_generation": "S0"}
    write(output / "preregistration.json", report)
    started, operation = time.monotonic(), 0
    confirmations = {}

    def check_fixed():
        if fixed != {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in fixed_files}:
            raise ExperimentStopped("frozen code changed")
        if (output / "STOP").exists():
            raise ExperimentStopped("stop requested; receipts preserved")
        if time.monotonic() - started > caps.seconds:
            raise ExperimentStopped("experiment time ceiling reached")

    def save():
        report["budget"] = ledger.snapshot()
        write(output / "report.json", report)

    def make_compiler(name, phase, arm, account):
        repo = isolate(REPO, native / phase / name / arm, name)
        ws = repo / "nonmatchings" / name
        receipts = []
        def compile_one(source, label, parent_receipt):
            nonlocal operation
            check_fixed()
            operation += 1
            charge = ledger.spend_evaluation if account == "evaluation" else ledger.spend
            try:
                charge(account, op=f"compile-{operation}", compiles=1)
            except BudgetExceeded as exc:
                raise ExperimentStopped(str(exc)) from exc
            verdict = evolution.compile_logged(ws, repo, name, source, conn=conn,
                strategy=f"search-evolution:{phase}:{arm}:{label}",
                run_id=f"search-evolution:{args.run_id}:{phase}:{name}:{arm}",
                parent_attempt_id=parent_receipt, action=label, model="deterministic-search",
                prompt="Generated development draft and current compiler observation; no reference implementation.",
                extra={"training_eligible": False, "assistance_tier": assistance[name],
                       "partition": account, "fixed_files_sha256": digest(fixed)})
            dump = ws / f"{name}_object_dump_normalized.s"
            verdict["dump"] = dump.read_text() if verdict["compiled"] and dump.exists() else None
            receipts.append({"receipt_id": verdict["receipt_id"], "source_sha256": digest(source),
                             "parent_receipt_id": parent_receipt, "label": label,
                             "compiled": verdict["compiled"], "exact": verdict["exact"], "score": verdict["score"],
                             "error": verdict.get("error")})
            return verdict
        return repo, ws, compile_one, receipts

    def scheduled(name, phase, policy, partition):
        repo, ws, compile_one, receipts = make_compiler(name, phase, policy.name, partition)
        context = {"task": name, "initial_sha256": digest(sources[name]),
            "target_sha256": hashlib.sha256((ws / "target.o").read_bytes()).hexdigest(),
            "compiler_sha256": compiler_identity(repo, ws), "generator_sha256": digest(fixed),
            "partition": partition, "assistance": assistance[name], "training_eligible": False}
        path = output / f"{phase}--{name}--{policy.name}.world.json"
        env = Online(sources[name], compile_one,
            lambda source, diff: regalloc_mutations.variants(source, name, diff), context,
            checkpoint=lambda w: save_world(w, path))
        result = run(env, policy, BUDGET)
        assert result == run(Replay(env.world), policy, BUDGET)
        assert compiler_identity(repo, ws) == context["compiler_sha256"]
        best = next(n for n in env.world["nodes"] if n["id"] == result["best_id"])
        row = {"function": name, "phase": phase, "policy": asdict(policy), **result,
               "world": path.name, "receipts": receipts, "source_sha256": best["source_sha256"]}
        report["runs"].append(row)
        if result["exact"]:
            confirmations[(name, best["source_sha256"])] = (best["source"], best["verdict"]["receipt_id"])
        save()
        if any(n["verdict"].get("error") for n in env.world["nodes"]):
            raise ExperimentStopped("infrastructure error in compiler observations")
        print(json.dumps({"function": name, "phase": phase, "policy": policy.name,
                          "exact": result["exact"], "compiles": result["compiles"]}), flush=True)
        return env.world, row

    def beam(name, phase):
        repo, ws, compile_one, receipts = make_compiler(name, phase, "beam", "evaluation")
        original = regalloc_mutations.variants
        parents, known = {}, {}
        def variants(code, function, diff="", prefer=()):
            for label, kind, child in original(code, function, diff, prefer):
                parents[digest(child)] = known[digest(code)]
                yield label, kind, child
        def callback(code, label):
            assert len(receipts) < BUDGET
            parent = parents.get(digest(code))
            assert label == "baseline" or parent is not None
            verdict = compile_one(code, label, parent)
            known[digest(code)] = verdict["receipt_id"]
            if verdict.get("error"):
                raise ExperimentStopped("beam compiler infrastructure error")
            return regalloc_search.Compiled(verdict["compiled"], verdict["exact"], verdict["dump"], verdict["diff"])
        regalloc_mutations.variants = variants
        try:
            baseline = callback(sources[name], "baseline")
            result = regalloc_search.search(name, sources[name], callback,
                (ws / "target_object_dump_normalized.s").read_text(), budget=BUDGET - 1,
                beam=3, depth=4, baseline=baseline)
        finally:
            regalloc_mutations.variants = original
        row = {"function": name, "phase": phase, "policy": {"name": "beam"}, "exact": result.exact,
               "compiles": len(receipts), "receipts": receipts, "source_sha256": digest(result.best_source)}
        (output / f"{phase}--{name}--beam.c").write_text(result.best_source)
        report["runs"].append(row)
        if result.exact:
            confirmations[(name, digest(result.best_source))] = (result.best_source, known[digest(result.best_source)])
        save()
        print(json.dumps({k: row[k] for k in ("function", "phase", "exact", "compiles")}), flush=True)
        return row

    def confirm_all():
        done = {(c["function"], c["source_sha256"]) for c in report["confirmations"]}
        for (name, sha), (source, parent_receipt) in confirmations.items():
            if (name, sha) in done:
                continue
            _, _, compile_one, _ = make_compiler(name, "confirm", sha[:12], "evaluation")
            verdict = compile_one(source, "independent-confirmation", parent_receipt)
            assert verdict["exact"]
            path = output / f"confirmed--{name}--{sha[:12]}.json"
            write(path, verdict)
            report["confirmations"].append({"function": name, "source_sha256": sha,
                "receipt_id": verdict["receipt_id"], "parent_receipt_id": parent_receipt,
                "exact": True, "certificate": path.name})
            save()

    def freeze(identity, parent_id, policy, selection_path=None):
        generation = gm.Generation(id=identity, parent=parent_id,
            created_at=datetime.now(timezone.utc).isoformat(), compositions=[asdict(policy)],
            verifier={str(p.relative_to(ROOT)): gm.hash_artifact(p) for p in fixed_files if p.parent.name == "solver"},
            evaluator={str(p.relative_to(ROOT)): gm.hash_artifact(p) for p in fixed_files if p.parent.name != "solver"},
            datasets={"preregistration": gm.hash_artifact(output / "preregistration.json")},
            training={"selection": gm.hash_artifact(selection_path)} if selection_path else {},
            budgets={"per_arm": BUDGET, **caps.as_dict()}, notes=report["claim_scope"])
        path = gm.freeze(output / "generations", generation)
        assert gm.verify(path.parent, path.stem)["verified"]
        return path

    parent = Policy("breadth", "breadth")
    generation_path = freeze("S0", None, parent)
    research_names = list(RESEARCH)
    retention_worlds = []
    for round_number, panel in enumerate(PANELS, 1):
        check_fixed()
        assert gm.verify(generation_path.parent, generation_path.stem)["verified"]
        if round_number == 2:
            research_names += ADDITIONAL_RESEARCH
        phase = f"r{round_number}-research"
        parent_worlds = [scheduled(name, phase, parent, "research")[0] for name in research_names]
        proposals = evolution.propose(parent_worlds, parent)
        write(output / f"r{round_number}-proposals.json", {"parent": asdict(parent),
            "proposals": [asdict(p) for p in proposals], "observations": [digest(w) for w in parent_worlds]})
        merged = []
        for name, world in zip(research_names, parent_worlds):
            branches = [world] + [scheduled(name, phase, p, "research")[0] for p in proposals]
            joined = merge_worlds(branches)
            save_world(joined, output / f"r{round_number}-research--{name}.world.json")
            merged.append(joined)
        selection = evolution.select(merged, parent, budget=BUDGET, proposals=proposals)
        selection_path = output / f"r{round_number}-selection.json"
        write(selection_path, selection)
        selected = Policy(**selection["candidate"])
        candidate_id = f"S{round_number}"
        candidate_path = freeze(candidate_id, report["active_generation"], selected, selection_path)
        selection_sha = hashlib.sha256(selection_path.read_bytes()).hexdigest()
        # No evaluation outcomes are read until the candidate and exclusions are frozen.
        evaluation_parent, evaluation_child, anchors = [], [], []
        for name in panel:
            pworld, _ = scheduled(name, f"r{round_number}-parent", parent, "evaluation")
            cworld, _ = scheduled(name, f"r{round_number}-candidate", selected, "evaluation")
            evaluation_parent.append(pworld)
            evaluation_child.append(cworld)
            anchors.append(beam(name, f"r{round_number}-anchor"))
        retention_child = [scheduled(w["context"]["task"], f"r{round_number}-retention", selected, "evaluation")[0]
                           for w in retention_worlds]
        decision = evolution.gate(evaluation_parent, evaluation_child, parent, selected,
                                 budget=BUDGET, research_tasks=set(RESEARCH + ADDITIONAL_RESEARCH),
                                 retention_parent=retention_worlds, retention_candidate=retention_child)
        beam_losses = [r["function"] for r, candidate_result in zip(anchors, decision["candidate_results"])
                       if r["exact"] and not candidate_result["exact"]]
        decision["beam_losses"] = beam_losses
        if beam_losses:
            decision.update(advance=False, reason="lost an existing beam-search exact")
        confirm_all()
        check_fixed()
        assert gm.verify(candidate_path.parent, candidate_path.stem)["verified"]
        assert gm.verify(generation_path.parent, generation_path.stem)["verified"]
        assert hashlib.sha256(selection_path.read_bytes()).hexdigest() == selection_sha
        assert before == {r[0] for r in upstream.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
        write(output / f"r{round_number}-decision.json", decision)
        report["rounds"].append({"round": round_number, "parent": report["active_generation"],
            "candidate": candidate_id, "selection_sha256": selection_sha, "decision": decision})
        if decision["advance"]:
            retention_worlds = retention_child + [w for w, result in zip(evaluation_child, decision["candidate_results"])
                                                  if result["exact"]]
            parent, generation_path = selected, candidate_path
            report["active_generation"] = candidate_id
        save()
        print(json.dumps({"round": round_number, "active_generation": report["active_generation"],
                          "advance": decision["advance"], "reason": decision["reason"]}), flush=True)
        if not decision["advance"]:
            break
    ledger.release("evaluation")
    report["total_compiles"] = conn.execute("SELECT count(*) FROM attempts").fetchone()[0]
    assert report["total_compiles"] == operation == sum(r["compiles"] for r in report["runs"]) + len(report["confirmations"])
    report["complete"] = True
    report["seconds"] = time.monotonic() - started
    save()
    conn.close()
    upstream.close()
    print(json.dumps({"complete": True, "active_generation": report["active_generation"],
                      "total_compiles": report["total_compiles"]}), flush=True)


if __name__ == "__main__":
    main()
