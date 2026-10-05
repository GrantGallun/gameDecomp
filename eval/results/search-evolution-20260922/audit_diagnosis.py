"""Audit the diagnosis follow-up against its frozen code and compiler receipts."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

OUT = Path(__file__).resolve().parent / "diagnosis-v1"
PRIOR = OUT.parent / "pilot-v2"
report = json.loads((OUT / "report.json").read_text())
assert report["complete"]
ROOT = Path(report["code_root"])
sys.path.insert(0, str(ROOT))
from eval import search_evolution as evolution
from eval.search_replay import Policy, Replay, digest, load_world, merge_worlds, run


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert report["training_eligible"] is False and report["production_mutated"] is False
    assert report["model_calls"] == 0
    assert all(file_sha(ROOT / p) == sha for p, sha in report["fixed_files"].items())
    assert file_sha(OUT / "selection.json") == report["selection_sha256"]
    selection = json.loads((OUT / "selection.json").read_text())
    parent, candidate = Policy(**report["parent"]), Policy(**report["candidate"])
    research = selection["selection"]["research_tasks"]
    def merged(name):
        return merge_worlds([load_world(p) for p in sorted(PRIOR.glob(f"*--{name}--*.world.json"))])
    inputs = [load_world(PRIOR / f"r1-research--{name}--breadth.world.json") for name in research]
    assert selection["proposal_inputs"] == [digest(w) for w in inputs]
    proposals = evolution.propose(inputs, parent)
    assert selection["proposals"] == [asdict(p) for p in proposals]
    recomputed = evolution.select([merged(name) for name in research], parent, budget=report["budget"],
        proposals=proposals, regressions=[{"kind": "development-regression", "world": merged("__MusIntProcessWobble")}])
    assert selection["selection"] == recomputed
    assert recomputed["candidate"] == asdict(candidate)
    assert set(report["diagnostic"]).isdisjoint(report["followup"])

    db = sqlite3.connect(f"file:{report['database']}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    attempts = {r["id"]: dict(r) for r in db.execute("SELECT * FROM attempts")}
    names = dict(db.execute("SELECT addr, name FROM functions"))
    for row in attempts.values():
        assert digest(row["source_code"]) == row["source_sha256"]
        meta = json.loads(row["sampling"])
        assert meta["training_eligible"] is False
        assert meta["fixed_files_sha256"] == digest(report["fixed_files"])
        if row["exact"]:
            certificate = meta["verification"]
            assert certificate["exact"] and certificate["status"] == "object_sections_exact"
            assert certificate["candidate_source_sha256"] == row["source_sha256"]
            assert certificate["frontend"]["passed"]
    edges = list(db.execute("SELECT * FROM attempt_edges"))
    for edge in edges:
        before, after = attempts[edge["parent_attempt_id"]], attempts[edge["child_attempt_id"]]
        assert before["func_addr"] == after["func_addr"] and after["parent_attempt_id"] == before["id"]
    assert {r["id"] for r in attempts.values() if r["parent_attempt_id"] is not None} == {
        e["child_attempt_id"] for e in edges}

    worlds, used = {}, []
    for row in report["runs"]:
        assert len(row["receipts"]) == row["compiles"] <= report["budget"]
        for receipt in row["receipts"]:
            stored = attempts[receipt["receipt_id"]]
            used.append(stored["id"])
            assert names[stored["func_addr"]] == row["function"]
            assert stored["source_sha256"] == receipt["source_sha256"]
            assert stored["parent_attempt_id"] == receipt["parent_receipt_id"]
            assert bool(stored["exact"]) == receipt["exact"]
            assert not receipt["error"]
        if "world" in row:
            world = load_world(OUT / row["world"])
            worlds[row["world"]] = world
            assert world["context"]["task"] == row["function"]
            assert world["context"]["initial_sha256"] == report["source_sha256"][row["function"]]
            assert world["context"]["generator_sha256"] == digest(report["fixed_files"])
            assert world["context"]["training_eligible"] is False
            assert len(world["nodes"]) == len(row["receipts"])
            replayed = run(Replay(world), Policy(**row["policy"]), report["budget"])
            for key, value in replayed.items():
                assert row[key] == value, (row["world"], key)
            for node, receipt in zip(world["nodes"], row["receipts"]):
                stored = attempts[node["verdict"]["receipt_id"]]
                assert stored["id"] == receipt["receipt_id"]
                assert stored["source_sha256"] == node["source_sha256"]
                assert stored["parent_attempt_id"] == node["parent_receipt_id"]
                assert bool(stored["exact"]) == node["verdict"]["exact"]
                assert bool(stored["compiled"]) == node["verdict"]["compiled"]
                assert stored["score"] == node["verdict"]["score"]
                assert stored["diff_summary"] == node["verdict"].get("raw_diff", node["verdict"]["diff"])
                assert json.loads(stored["sampling"]).get("verification") == node["verdict"].get("verification")
                assert not node["verdict"].get("error")
            best = next(n for n in world["nodes"] if n["id"] == row["best_id"])
            assert best["source_sha256"] == row["source_sha256"]
        else:
            assert row["phase"] == "followup" and row["policy"]["name"] == "beam"
            exact = [r for r in row["receipts"] if r["exact"]]
            assert row["exact"] == bool(exact)
            assert digest((OUT / f"followup--{row['function']}--beam.c").read_text()) == row["source_sha256"]
            assert any(r["source_sha256"] == row["source_sha256"] for r in row["receipts"])
            if exact:
                assert any(r["source_sha256"] == row["source_sha256"] for r in exact)

    confirmed = set()
    for row in report["confirmations"]:
        stored = attempts[row["receipt_id"]]
        used.append(stored["id"])
        assert stored["exact"] and names[stored["func_addr"]] == row["function"]
        assert stored["source_sha256"] == row["source_sha256"]
        assert stored["parent_attempt_id"] == row["parent_receipt_id"]
        verdict = json.loads((OUT / row["certificate"]).read_text())
        assert verdict["exact"] and verdict["receipt_id"] == row["receipt_id"] and not verdict.get("error")
        cert = verdict["verification"]
        assert cert["candidate_source_sha256"] == row["source_sha256"]
        assert verdict["frontend"]["passed"] and verdict["frontend"]["source_sha256"] == cert["source_sha256"]
        targets = {w["context"]["target_sha256"] for w in worlds.values() if w["context"]["task"] == row["function"]}
        assert targets == {cert["target_sha256"]}
        confirmed.add((row["function"], row["source_sha256"]))
    assert {(r["function"], r["source_sha256"]) for r in report["runs"] if r["exact"]} == confirmed
    assert len(used) == len(set(used)) == len(attempts) and set(used) == set(attempts)

    def panel(phase, policy):
        return [worlds[r["world"]] for r in report["runs"] if r["phase"] == phase and r["policy"]["name"] == policy.name]
    for name in report["diagnostic"]:
        merge_worlds([w for w in panel("diagnostic", parent) + panel("diagnostic", candidate) if w["context"]["task"] == name])
    decision = evolution.gate(panel("followup", parent), panel("followup", candidate), parent, candidate,
        budget=report["budget"], research_tasks=set(report["diagnostic"]))
    old = json.loads((PRIOR / "report.json").read_text())
    known = {r["function"] for r in old["runs"] if r["exact"]}
    for name in known:
        assert any(n["verdict"]["exact"] for n in merged(name)["nodes"])
    replicated = {r["function"] for r in report["runs"] if r["phase"] == "diagnostic" and r["policy"]["name"] == candidate.name and r["exact"]}
    decision["diagnostic_known_losses"] = sorted(known - replicated)
    if decision["diagnostic_known_losses"]:
        decision.update(advance=False, reason="lost a previously known development exact")
    anchors = [r for r in report["runs"] if r["policy"]["name"] == "beam"]
    assert [r["function"] for r in anchors] == report["followup"]
    decision["beam_losses"] = [a["function"] for a, r in zip(anchors, decision["candidate_results"]) if a["exact"] and not r["exact"]]
    if decision["beam_losses"]:
        decision.update(advance=False, reason="lost existing beam exact")
    assert decision == report["followup_gate"]
    expected = sum(r["compiles"] for r in report["runs"]) + len(confirmed)
    assert expected == report["total_compiles"] == len(attempts) == report["ledger"]["spent"]["evaluation_compiles"]
    assert not report["ledger"]["overruns"]
    summary = {"complete": True, "attempts": len(attempts), "explicit_parent_edges": len(edges),
        "worlds": len(worlds), "independent_confirmations": len(confirmed), "selection_reproduced": True,
        "all_bindings_valid": True, "known_exacts": sorted(known), "known_losses": decision["diagnostic_known_losses"],
        "followup_advance": decision["advance"], "training_eligible": False}
    (OUT / "audit.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
