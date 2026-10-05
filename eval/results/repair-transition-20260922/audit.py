"""Recompute controller decisions and bind every result to its durable receipt."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

SHARED = Path(__file__).resolve().parent
OUT = SHARED / "paired"
report = json.loads((OUT / "report.json").read_text())
CODE = Path(report["code_root"])
sys.path.insert(0,str(CODE))
from eval.repair_graph import build_graph, validate_graph
from eval.repair_planner import replay_planner
from eval.repair_transitions import validate_model
from eval.search_replay import Policy, Replay, digest, load_world, run
from solver import regalloc_mutations
from solver.repair_rules import state_features


def main():
    assert report["complete"] and not report["training_eligible"] and not report["production_mutated"]
    assert report["model_calls"] == 0 and report["main_kb_exact_set_unchanged"]
    for p,sha in report["artifacts"].items():
        assert hashlib.sha256((SHARED/p).read_bytes()).hexdigest() == sha
    for p,sha in report["fixed_files"].items():
        assert hashlib.sha256((CODE/p).read_bytes()).hexdigest() == sha
    frozen = json.loads((SHARED / "freeze.json").read_text())
    worlds = []
    for relative,sha in frozen["development_worlds"].items():
        world = load_world(SHARED.parent/relative)
        assert digest(world) == sha
        worlds.append(world)
    graph = validate_graph(json.loads((SHARED / "development-graph.json").read_text()))
    assert graph == build_graph(worlds)
    model = validate_model(json.loads((SHARED / "model.json").read_text()),graph=graph)
    assert model["sha256"] == report["model_sha256"] == frozen["model_sha256"]
    db = sqlite3.connect(f"file:{report['database']}?mode=ro",uri=True)
    db.row_factory = sqlite3.Row
    attempts = {r["id"]:dict(r) for r in db.execute("SELECT * FROM attempts")}
    names = dict(db.execute("SELECT addr,name FROM functions"))
    edges = list(db.execute("SELECT * FROM attempt_edges"))
    for stored in attempts.values():
        assert digest(stored["source_code"]) == stored["source_sha256"]
        meta = json.loads(stored["sampling"])
        assert meta["training_eligible"] is False
        assert meta["fixed_files_sha256"] == digest(report["fixed_files"])
        assert meta["transition_model_sha256"] == model["sha256"]
    for edge in edges:
        parent,child = attempts[edge["parent_attempt_id"]],attempts[edge["child_attempt_id"]]
        assert parent["func_addr"] == child["func_addr"] and child["parent_attempt_id"] == parent["id"]
    assert {r["id"] for r in attempts.values() if r["parent_attempt_id"] is not None} == {r["child_attempt_id"] for r in edges}
    def bind(verdict,stored):
        assert verdict["compiled"] == bool(stored["compiled"])
        assert verdict["exact"] == bool(stored["exact"])
        assert verdict["score"] == stored["score"] and not verdict.get("error")
        assert verdict.get("raw_diff",verdict.get("diff","")) == stored["diff_summary"]
        metadata = json.loads(stored["sampling"])
        for key in ("verification","frontend","compiler_recipe","source_attribution"):
            assert verdict.get(key) == metadata.get(key)
    used, live_worlds, coverage = [], {}, []
    for row in report["runs"]:
        world = load_world(OUT / row["world"])
        validate_graph(build_graph([world]))
        live_worlds[(row["function"],row["arm"])] = world
        ctx = world["context"]
        assert ctx["task"] == row["function"] and ctx["initial_sha256"] == report["source_sha256"][row["function"]]
        assert ctx["generator_sha256"] == digest(report["fixed_files"])
        assert (ctx["target_sha256"] in model["development_targets"]) == (row["phase"] == "development")
        if row["arm"] == "control":
            replayed = run(Replay(world),Policy(**report["policy"]),report["budget"])
        else:
            def variants(source,diff):
                yield from regalloc_mutations.variants(source,row["function"],diff)
            replayed = replay_planner(world,model,variants,budget=report["budget"],horizon=report["horizon"],
                                      preview=report["preview"],fallback=Policy(**report["policy"]))
            coverage.append({"function":row["function"],"phase":row["phase"],"root":state_features(
                world["nodes"][0]["source"],row["function"],world["nodes"][0]["verdict"]),
                "guided_decisions":row["guided_decisions"],
                "supported_decisions":sum(d["prediction"]["support_targets"] > 0 for d in row["decisions"]),
                "observed_families":sorted({n["family"] for n in world["nodes"]}),
                "stop":row["stop"]})
        assert all(row[k] == v for k,v in replayed.items())
        assert len(world["nodes"]) == len(row["receipts"]) == row["compiles"] <= report["budget"]
        nodes = {n["id"]:n for n in world["nodes"]}
        for node,receipt in zip(world["nodes"],row["receipts"]):
            stored = attempts[node["verdict"]["receipt_id"]]
            bind(node["verdict"],stored)
            assert names[stored["func_addr"]] == row["function"]
            assert stored["source_sha256"] == node["source_sha256"] == receipt["source_sha256"]
            assert stored["source_code"] == node["source"] and stored["id"] == receipt["receipt_id"]
            assert stored["parent_attempt_id"] == node["parent_receipt_id"] == receipt["parent_receipt_id"]
            assert stored["parent_attempt_id"] == (nodes[node["parent"]]["verdict"]["receipt_id"] if node["parent"] else None)
            used.append(stored["id"])
        assert nodes[row["best_id"]]["source_sha256"] == row["source_sha256"]
        assert digest((OUT / row["world"].replace(".world.json",".c")).read_text()) == row["source_sha256"]
    confirmed = set()
    for row in report["confirmations"]:
        stored = attempts[row["receipt_id"]]
        verdict = json.loads((OUT / row["certificate"]).read_text())
        bind(verdict,stored)
        cert = verdict["verification"]
        assert stored["exact"] and names[stored["func_addr"]] == row["function"]
        assert stored["source_sha256"] == row["source_sha256"] == cert["candidate_source_sha256"]
        assert stored["parent_attempt_id"] == row["parent_receipt_id"]
        assert cert["target_sha256"] == live_worlds[(row["function"],"planner")]["context"]["target_sha256"]
        assert cert["exact"] and cert["status"] == "object_sections_exact"
        assert verdict["frontend"]["passed"] and verdict["frontend"]["source_sha256"] == cert["source_sha256"]
        used.append(stored["id"])
        confirmed.add((row["function"],row["source_sha256"]))
    assert confirmed == {(r["function"],r["source_sha256"]) for r in report["runs"] if r["exact"]}
    assert len(used) == len(set(used)) == len(attempts) and set(used) == set(attempts)
    comparisons = []
    for case in report["cases"]:
        a,b = [r for r in report["runs"] if r["function"] == case["function"]]
        assert live_worlds[(case["function"],"control")]["context"] == live_worlds[(case["function"],"planner")]["context"]
        comparisons.append({**case,"gained":b["exact"] and not a["exact"],"lost":a["exact"] and not b["exact"],
            "control_exact":a["exact"],"planner_exact":b["exact"],"control_compiles":a["compiles"],"planner_compiles":b["compiles"]})
    assert comparisons == report["comparison"]
    assert report["followup_gains"] == [r["function"] for r in comparisons if r["phase"] == "followup" and r["gained"]]
    assert report["known_losses"] == [r["function"] for r in comparisons if r["phase"] in {"development","retention"} and not r["planner_exact"]]
    assert report["losses"] == [r["function"] for r in comparisons if r["lost"]]
    assert report["total_compiles"] == len(attempts) == sum(r["compiles"] for r in report["runs"])+len(confirmed)
    assert len(attempts) == report["ledger"]["spent"]["evaluation_compiles"] and not report["ledger"]["overruns"]
    summary = {"complete":True,"paired_compiles":sum(r["compiles"] for r in report["runs"]),
        "independent_confirmations":len(confirmed),"total_compiles":len(attempts),
        "explicit_parent_edges":len(edges),"worlds":len(live_worlds),"all_receipt_bindings_valid":True,
        "decisions_recomputed":True,"model_recomputed_from_declared_development":True,
        "followup_gains":report["followup_gains"],"known_losses":report["known_losses"],
        "training_eligible":False,"coverage":coverage}
    (OUT / "audit.json").write_text(json.dumps(summary,indent=2))
    print(json.dumps({k:v for k,v in summary.items() if k != "coverage"},indent=2))


if __name__ == "__main__":
    main()
