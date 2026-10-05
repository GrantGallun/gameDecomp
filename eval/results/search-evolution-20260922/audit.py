"""Read-only audit of live policy worlds, certificates, cost and succession."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run")
    args = parser.parse_args()
    output = Path(__file__).resolve().parent / args.run
    report = json.loads((output / "report.json").read_text())
    assert report["complete"]
    # Reproduce historical selection with the measured algorithm, even after
    # the working tree's proposer or selection result format has evolved.
    code_root = Path(report["code_root"])
    for relative, expected in report["fixed_files"].items():
        assert hashlib.sha256((code_root / relative).read_bytes()).hexdigest() == expected
    sys.path.insert(0, str(code_root))
    from eval.search_replay import Policy, digest, load_world, run, Replay
    from eval import generation_manifest as gm, search_evolution as evolution

    db = sqlite3.connect(f"file:{report['database']}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    attempts = {r["id"]: dict(r) for r in db.execute("SELECT * FROM attempts")}
    for row in attempts.values():
        assert digest(row["source_code"]) == row["source_sha256"]
        meta = json.loads(row["sampling"])
        assert meta["training_eligible"] is False
        # Catch the exception shape of the original logging adapter explicitly.
        assert not (not row["compiled"] and not meta.get("verification") and
                    any(word in (row["compiler_stderr"] or "").split(":", 1)[0]
                        for word in ("Error", "Exception"))), row["id"]
        if row["exact"]:
            certificate = meta["verification"]
            assert certificate["exact"] and certificate["status"] == "object_sections_exact"
            assert certificate["candidate_source_sha256"] == row["source_sha256"]
            assert certificate["frontend"]["passed"]
    edges = list(db.execute("SELECT * FROM attempt_edges"))
    for edge in edges:
        parent, child = attempts[edge["parent_attempt_id"]], attempts[edge["child_attempt_id"]]
        assert parent["func_addr"] == child["func_addr"] and child["parent_attempt_id"] == parent["id"]
    assert {r["id"] for r in attempts.values() if r["parent_attempt_id"] is not None} == {
        e["child_attempt_id"] for e in edges}
    worlds = {}
    for row in report["runs"]:
        assert len(row["receipts"]) == row["compiles"] <= report["per_arm_compile_ceiling"]
        for receipt in row["receipts"]:
            stored = attempts[receipt["receipt_id"]]
            assert stored["source_sha256"] == receipt["source_sha256"]
            assert stored["parent_attempt_id"] == receipt["parent_receipt_id"]
            assert bool(stored["exact"]) == receipt["exact"]
        if "world" in row:
            w = load_world(output / row["world"])
            worlds[row["world"]] = w
            replayed = run(Replay(w), Policy(**row["policy"]), report["per_arm_compile_ceiling"])
            for key in ("exact", "compiles", "trace", "best_id", "complete"):
                assert replayed[key] == row[key]
            for node in w["nodes"]:
                stored = attempts[node["verdict"]["receipt_id"]]
                assert stored["source_sha256"] == node["source_sha256"]
                assert stored["parent_attempt_id"] == node["parent_receipt_id"]
                assert bool(stored["compiled"]) == node["verdict"]["compiled"]
                assert bool(stored["exact"]) == node["verdict"]["exact"]
                assert stored["score"] == node["verdict"]["score"]
                assert stored["diff_summary"] == node["verdict"].get("raw_diff", node["verdict"]["diff"])
                assert json.loads(stored["sampling"]).get("verification") == node["verdict"].get("verification")
        else:
            assert row["policy"]["name"] == "beam"
            exact_receipts = [attempts[r["receipt_id"]] for r in row["receipts"] if r["exact"]]
            assert row["exact"] == bool(exact_receipts)
            assert any(attempts[r["receipt_id"]]["source_sha256"] == row["source_sha256"] for r in row["receipts"])
            if row["exact"]:
                assert any(r["source_sha256"] == row["source_sha256"] for r in exact_receipts)
    confirmed = set()
    for row in report["confirmations"]:
        stored = attempts[row["receipt_id"]]
        assert stored["exact"] and stored["source_sha256"] == row["source_sha256"]
        verdict = json.loads((output / row["certificate"]).read_text())
        assert verdict["verification"]["candidate_source_sha256"] == row["source_sha256"]
        assert verdict["exact"] and verdict["receipt_id"] == row["receipt_id"]
        confirmed.add((row["function"], row["source_sha256"]))
    assert {(r["function"], r["source_sha256"]) for r in report["runs"] if r["exact"]} <= confirmed
    active = "S0"
    retained = []
    for row in report["rounds"]:
        round_number = row["round"]
        assert row["parent"] == active
        selection_path = output / f"r{round_number}-selection.json"
        assert hashlib.sha256(selection_path.read_bytes()).hexdigest() == row["selection_sha256"]
        selection = json.loads(selection_path.read_text())
        parent, candidate = Policy(**selection["parent"]), Policy(**selection["candidate"])
        research = [load_world(output / f"r{round_number}-research--{name}.world.json") for name in selection["research_tasks"]]
        proposals = json.loads((output / f"r{round_number}-proposals.json").read_text())
        recomputed = evolution.select(research, parent, budget=report["per_arm_compile_ceiling"],
                                      proposals=[Policy(**p) for p in proposals["proposals"]])
        assert recomputed == selection
        parent_worlds, candidate_worlds = [], []
        for run_row in report["runs"]:
            if run_row["phase"] == f"r{round_number}-parent": parent_worlds.append(worlds[run_row["world"]])
            if run_row["phase"] == f"r{round_number}-candidate": candidate_worlds.append(worlds[run_row["world"]])
        decision = evolution.gate(parent_worlds, candidate_worlds, parent, candidate,
            budget=report["per_arm_compile_ceiling"], research_tasks=set(report["research"] + report["additional_research"]),
            retention_parent=retained,
            retention_candidate=[worlds[r["world"]] for r in report["runs"] if r["phase"] == f"r{round_number}-retention"])
        candidate_results = dict(zip([w["context"]["task"] for w in parent_worlds], decision["candidate_results"]))
        anchors = [r for r in report["runs"] if r["phase"] == f"r{round_number}-anchor"]
        assert len(anchors) == len(parent_worlds)
        assert {r["function"] for r in anchors} == set(candidate_results)
        beam_losses = [r["function"] for r in anchors if r["exact"] and not candidate_results[r["function"]]["exact"]]
        assert row["decision"]["beam_losses"] == beam_losses
        if beam_losses:
            decision.update(advance=False, reason="lost an existing beam-search exact")
        for key in decision: assert row["decision"][key] == decision[key], key
        assert gm.verify(output / "generations", row["candidate"])["verified"]
        if decision["advance"]:
            active = row["candidate"]
            retained = [worlds[r["world"]] for r in report["runs"] if r["phase"] == f"r{round_number}-retention"]
            retained += [w for w, result in zip(candidate_worlds, decision["candidate_results"]) if result["exact"]]
    assert active == report["active_generation"]
    assert gm.verify(output / "generations", "S0")["verified"]
    expected = sum(r["compiles"] for r in report["runs"]) + len(report["confirmations"])
    spent = report["budget"]["spent"]
    assert len(attempts) == expected == report["total_compiles"] == spent.get("compiles", 0) + spent.get("evaluation_compiles", 0)
    summary = {"complete": True, "attempts": len(attempts), "explicit_parent_edges": len(edges),
               "independent_confirmations": len(confirmed), "active_generation": active,
               "all_bindings_valid": True, "training_eligible": False}
    (output / "audit.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
