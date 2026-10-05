"""Replay the frozen experiment and bind theory claims to compiler receipts."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

SHARED = Path(__file__).resolve().parent
OUT = SHARED / "paired"
report = json.loads((OUT / "report.json").read_text())
CODE = Path(report["code_root"])
sys.path.insert(0, str(CODE))
from eval.repair_graph import _bindings, build_graph, validate_graph
from eval.repair_planner import ActionOnline, replay_planner
from eval.repair_transitions import validate_model
from eval.search_replay import Policy, digest, load_world
from eval.theory_planner import TheoryOnline
from solver import regalloc_mutations
from solver.repair_theory import effect, observation, validate_map
from solver.theory_repairs import inspect


def write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False))


def main():
    assert report["complete"] and not report["training_eligible"] and not report["production_mutated"]
    assert report["model_calls"] == 0 and report["main_kb_exact_set_unchanged"]
    for p, sha in report["artifacts"].items():
        assert hashlib.sha256((SHARED / p).read_bytes()).hexdigest() == sha
    for p, sha in report["fixed_files"].items():
        assert hashlib.sha256((CODE / p).read_bytes()).hexdigest() == sha
    frozen = json.loads((SHARED / "freeze.json").read_text())
    development = []
    for relative, sha in frozen["development_worlds"].items():
        world = load_world(SHARED.parent / relative)
        assert digest(world) == sha
        development.append(world)
    graph = validate_graph(json.loads((SHARED / "development-graph.json").read_text()))
    assert graph == build_graph(development)
    model = validate_model(json.loads((SHARED / "model.json").read_text()), graph=graph)
    assert model["sha256"] == report["model_sha256"] == frozen["model_sha256"]
    db = sqlite3.connect(f"file:{report['database']}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    attempts = {r["id"]: dict(r) for r in db.execute("SELECT * FROM attempts")}
    names = dict(db.execute("SELECT addr,name FROM functions"))
    edges = list(db.execute("SELECT * FROM attempt_edges"))
    for stored in attempts.values():
        assert digest(stored["source_code"]) == stored["source_sha256"]
        meta = json.loads(stored["sampling"])
        assert meta["training_eligible"] is False
        assert meta["fixed_files_sha256"] == digest(report["fixed_files"])
        assert meta["transition_model_sha256"] == model["sha256"]
    for edge in edges:
        parent, child = attempts[edge["parent_attempt_id"]], attempts[edge["child_attempt_id"]]
        assert parent["func_addr"] == child["func_addr"] and child["parent_attempt_id"] == parent["id"]
    assert {r["id"] for r in attempts.values() if r["parent_attempt_id"] is not None} == {r["child_attempt_id"] for r in edges}

    def bind(verdict, stored):
        assert verdict["compiled"] == bool(stored["compiled"])
        assert verdict["exact"] == bool(stored["exact"])
        assert verdict["score"] == stored["score"] and not verdict.get("error")
        assert verdict.get("raw_diff", verdict.get("diff", "")) == stored["diff_summary"]
        metadata = json.loads(stored["sampling"])
        for key in ("verification", "frontend", "compiler_recipe", "source_attribution"):
            assert verdict.get(key) == metadata.get(key)

    used, live_worlds, coverage, exported = [], {}, [], []
    local_results = Counter()
    for row in report["runs"]:
        world = load_world(OUT / row["world"])
        validate_graph(build_graph([world]))
        live_worlds[(row["function"], row["arm"])] = world
        ctx = world["context"]
        assert ctx["task"] == row["function"] and ctx["initial_sha256"] == report["source_sha256"][row["function"]]
        assert ctx["generator_sha256"] == digest(report["fixed_files"])
        assert (ctx["target_sha256"] in model["development_targets"]) == (row["phase"] == "development")
        repo = Path(report["database"]).parent / row["function"] / row["arm"]
        workspace = repo / "nonmatchings" / row["function"]

        def variants(source, diff):
            yield from regalloc_mutations.variants(source, row["function"], diff)

        def inspector(source, verdict):
            return inspect(source, row["function"], verdict, repo=repo, workspace=workspace)

        def factory(*args, **kwargs):
            if row["arm"] == "baseline":
                return ActionOnline(*args, **kwargs)
            return TheoryOnline(*args, **kwargs, inspect_routes=inspector, guide=row["arm"] == "theory")

        replayed = replay_planner(world, model, variants, budget=report["budget"], horizon=report["horizon"],
            preview=report["preview"], fallback=Policy(**report["policy"]), environment_factory=factory)
        assert all(row[k] == v for k, v in replayed.items())
        assert len(world["nodes"]) == len(row["receipts"]) == row["compiles"] <= report["budget"]
        nodes = {n["id"]: n for n in world["nodes"]}
        for node, receipt in zip(world["nodes"], row["receipts"]):
            stored = attempts[node["verdict"]["receipt_id"]]
            bind(node["verdict"], stored)
            assert names[stored["func_addr"]] == row["function"]
            assert stored["source_sha256"] == node["source_sha256"] == receipt["source_sha256"]
            assert stored["source_code"] == node["source"] and stored["id"] == receipt["receipt_id"]
            assert stored["parent_attempt_id"] == node["parent_receipt_id"] == receipt["parent_receipt_id"]
            assert stored["parent_attempt_id"] == (nodes[node["parent"]]["verdict"]["receipt_id"] if node["parent"] else None)
            used.append(stored["id"])
        assert nodes[row["best_id"]]["source_sha256"] == row["source_sha256"]
        assert digest((OUT / row["world"].replace(".world.json", ".c")).read_text()) == row["source_sha256"]
        if "theory" not in row:
            continue
        theory = row["theory"]
        assert not theory["training_eligible"] and not theory["global_impossibility_established"]
        for parent, mapping in theory["maps"].items():
            validate_map(mapping)
            assert mapping["inputs"]["source"] == nodes[parent]["source"]
            assert mapping["inputs"]["verdict"] == nodes[parent]["verdict"]
        for outcome in theory["effects"]:
            parent, child = nodes[outcome["parent"]], nodes[outcome["child"]]
            assert outcome["parent_receipt_id"] == parent["verdict"]["receipt_id"] == child["parent_receipt_id"]
            assert outcome["receipt_id"] == child["verdict"]["receipt_id"]
            assert outcome["parent_source_sha256"] == parent["source_sha256"]
            assert outcome["source_sha256"] == child["source_sha256"]
            actual = effect(parent["verdict"], child["verdict"], outcome["addresses"])
            assert all(outcome[k] == v for k, v in actual.items())
            local_results[outcome["local_result"]] += 1
        best = nodes[theory["best_intake_id"]]
        item = {"function": row["function"], "phase": row["phase"], "arm": row["arm"],
            "root": observation(nodes["root"]["verdict"]), "best_intake": observation(best["verdict"]),
            "best_intake_id": best["id"], "best_intake_receipt_id": best["verdict"]["receipt_id"],
            "theory_decisions": sum(d["reason"] == "theory-alternative" for d in row["decisions"]),
            "effects": theory["effects"], "goal_status": theory["goal_status"],
            "route_outcomes": theory["route_outcomes"], "suppressed": len(theory["suppressed"])}
        coverage.append(item)
        if row["arm"] == "theory" and row["phase"] == "followup":
            source_file = f"best-intake--{row['function']}.c"
            (SHARED / source_file).write_text(best["source"])
            exported.append({**item, "context": ctx, "world": "paired/" + row["world"],
                "maps": theory["maps"], "best_intake_source": source_file,
                "best_intake_source_sha256": best["source_sha256"]})
    confirmed = set()
    for row in report["confirmations"]:
        stored = attempts[row["receipt_id"]]
        verdict = json.loads((OUT / row["certificate"]).read_text())
        bind(verdict, stored)
        matching_world = live_worlds[(row["function"], "theory")]
        _bindings({"source": stored["source_code"], "source_sha256": stored["source_sha256"],
                   "verdict": verdict}, matching_world["context"])
        parent = attempts[row["parent_receipt_id"]]
        assert parent["exact"] and parent["source_sha256"] == stored["source_sha256"]
        assert parent["id"] in {n["verdict"]["receipt_id"] for n in matching_world["nodes"]
                                if n["verdict"]["exact"] and n["source_sha256"] == stored["source_sha256"]}
        cert = verdict["verification"]
        assert stored["exact"] and names[stored["func_addr"]] == row["function"]
        assert stored["source_sha256"] == row["source_sha256"] == cert["candidate_source_sha256"]
        assert stored["parent_attempt_id"] == row["parent_receipt_id"]
        assert cert["target_sha256"] == live_worlds[(row["function"], "theory")]["context"]["target_sha256"]
        assert cert["exact"] and cert["status"] == "object_sections_exact"
        assert verdict["frontend"]["passed"] and verdict["frontend"]["source_sha256"] == cert["source_sha256"]
        used.append(stored["id"])
        confirmed.add((row["function"], row["source_sha256"]))
    assert confirmed == {(r["function"], r["source_sha256"]) for r in report["runs"] if r["exact"]}
    assert len(used) == len(set(used)) == len(attempts) and set(used) == set(attempts)
    comparisons = []
    for case in report["cases"]:
        name = case["function"]
        rows = {r["arm"]: r for r in report["runs"] if r["function"] == name}
        assert set(rows) == {"baseline", "intake", "theory"}
        assert live_worlds[(name, "baseline")]["context"] == live_worlds[(name, "intake")]["context"] == live_worlds[(name, "theory")]["context"]
        comparisons.append({**case, "arms": {a: {k: r[k] for k in ("exact", "compiles", "best_score")} for a, r in rows.items()}})
    assert comparisons == report["comparison"]
    assert report["followup_gains"] == [r["function"] for r in comparisons if r["arms"]["theory"]["exact"] and not r["arms"]["baseline"]["exact"]]
    assert report["known_losses"] == [r["function"] for r in report["runs"] if r["phase"] in {"development", "retention"} and not r["exact"]]
    assert report["losses"] == [r["function"] for r in comparisons if r["arms"]["baseline"]["exact"] and not r["arms"]["theory"]["exact"]]
    assert report["total_compiles"] == len(attempts) == sum(r["compiles"] for r in report["runs"]) + len(confirmed)
    assert len(attempts) == report["ledger"]["spent"]["evaluation_compiles"] and not report["ledger"]["overruns"]
    same_sequences = []
    for case in report["cases"]:
        name = case["function"]
        def sequence(arm):
            return [(n["source_sha256"], n["label"], n["parent"]) for n in live_worlds[(name, arm)]["nodes"]]
        if sequence("intake") == sequence("theory"):
            same_sequences.append(name)
    summary = {"complete": True, "paired_compiles": sum(r["compiles"] for r in report["runs"]),
        "independent_confirmations": len(confirmed), "total_compiles": len(attempts),
        "explicit_parent_edges": len(edges), "worlds": len(live_worlds), "all_receipt_bindings_valid": True,
        "decisions_recomputed": True, "theory_maps_recomputed": True,
        "model_recomputed_from_declared_development": True,
        "followup_gains": report["followup_gains"], "known_losses": report["known_losses"],
        "intake_theory_identical_sequences": same_sequences,
        "local_effects_both_intake_arms": dict(local_results), "training_eligible": False,
        "report_sha256": digest(report), "audit_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "coverage": coverage}
    write(OUT / "audit.json", summary)
    write(SHARED / "theory-map.json", {"schema_version": 1, "training_eligible": False,
        "report_sha256": digest(report), "feasibility": "conditional-not-proven", "cases": exported})
    print(json.dumps({k: v for k, v in summary.items() if k != "coverage"}, indent=2))


if __name__ == "__main__":
    main()
