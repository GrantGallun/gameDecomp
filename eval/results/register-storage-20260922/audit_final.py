"""Bind the paired repair results, costs and causal scores to compile receipts."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

SHARED = Path(__file__).resolve().parent
OUT = SHARED / "paired-v3"
report = json.loads((OUT / "report.json").read_text())
assert report["complete"]
CODE = Path(report["code_root"])
sys.path.insert(0, str(CODE))
from eval.search_replay import Policy, Replay, digest, load_world, run


def attempts_at(path):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    attempts = {r["id"]: dict(r) for r in db.execute("SELECT * FROM attempts")}
    names = dict(db.execute("SELECT addr,name FROM functions"))
    for row in attempts.values():
        assert digest(row["source_code"]) == row["source_sha256"]
        meta = json.loads(row["sampling"])
        assert meta["training_eligible"] is False
        if row["exact"]:
            cert = meta["verification"]
            assert cert["exact"] and cert["status"] == "object_sections_exact"
            assert cert["candidate_source_sha256"] == row["source_sha256"]
            assert cert["frontend"]["passed"]
    edges = list(db.execute("SELECT * FROM attempt_edges"))
    for edge in edges:
        a, b = attempts[edge["parent_attempt_id"]], attempts[edge["child_attempt_id"]]
        assert a["func_addr"] == b["func_addr"] and b["parent_attempt_id"] == a["id"]
    assert {r["id"] for r in attempts.values() if r["parent_attempt_id"] is not None} == {r["child_attempt_id"] for r in edges}
    return db, attempts, names, edges


def bind(verdict, stored):
    assert verdict["compiled"] == bool(stored["compiled"])
    assert verdict["exact"] == bool(stored["exact"])
    assert verdict["score"] == stored["score"]
    assert verdict.get("raw_diff", verdict["diff"]) == stored["diff_summary"]
    assert verdict.get("verification") == json.loads(stored["sampling"]).get("verification")
    assert not verdict.get("error")


def main():
    assert not report["training_eligible"] and not report["production_mutated"] and report["model_calls"] == 0
    assert hashlib.sha256((SHARED / "measure_final.py").read_bytes()).hexdigest() == report["driver_sha256"]
    assert hashlib.sha256((SHARED / "panel-v2.json").read_bytes()).hexdigest() == report["panel_sha256"]
    for p, sha in report["fixed_files"].items():
        assert hashlib.sha256((CODE / p).read_bytes()).hexdigest() == sha
    db, attempts, names, edges = attempts_at(report["database"])
    worlds, used = {}, []
    for row in report["runs"]:
        world = load_world(OUT / row["world"])
        worlds[(row["function"], row["arm"])] = world
        assert world["context"]["task"] == row["function"]
        assert world["context"]["initial_sha256"] == report["source_sha256"][row["function"]]
        assert world["context"]["generator_sha256"] == digest({"files": report["fixed_files"], "arm": row["arm"]})
        replayed = run(Replay(world), Policy(**report["policy"]), report["budget"])
        assert all(row[k] == v for k,v in replayed.items())
        assert len(world["nodes"]) == len(row["receipts"]) == row["compiles"] <= report["budget"]
        nodes = {n["id"]: n for n in world["nodes"]}
        for node, receipt in zip(world["nodes"], row["receipts"]):
            stored = attempts[node["verdict"]["receipt_id"]]
            bind(node["verdict"], stored)
            assert names[stored["func_addr"]] == row["function"]
            assert json.loads(stored["sampling"])["fixed_files_sha256"] == digest(report["fixed_files"])
            assert stored["source_sha256"] == node["source_sha256"] == receipt["source_sha256"]
            assert stored["id"] == receipt["receipt_id"]
            assert stored["parent_attempt_id"] == node["parent_receipt_id"] == receipt["parent_receipt_id"]
            assert stored["parent_attempt_id"] == (nodes[node["parent"]]["verdict"]["receipt_id"] if node["parent"] else None)
            used.append(stored["id"])
        assert nodes[row["best_id"]]["source_sha256"] == row["source_sha256"]
        assert digest((OUT / row["world"].replace(".world.json", ".c")).read_text()) == row["source_sha256"]
        calls = sum(n["family"] in {"parameter_reuse"} for n in nodes.values())
        assert calls == row["new_family_calls"] and (row["arm"] != "control" or calls == 0)
    confirmed = set()
    for row in report["confirmations"]:
        stored = attempts[row["receipt_id"]]
        verdict = json.loads((OUT / row["certificate"]).read_text())
        bind(verdict, stored)
        assert stored["exact"] and stored["source_sha256"] == row["source_sha256"]
        assert names[stored["func_addr"]] == row["function"]
        assert stored["parent_attempt_id"] == row["parent_receipt_id"]
        assert verdict["verification"]["target_sha256"] == worlds[(row["function"], "expanded")]["context"]["target_sha256"]
        assert verdict["frontend"]["passed"] and verdict["frontend"]["source_sha256"] == verdict["verification"]["source_sha256"]
        used.append(stored["id"])
        confirmed.add((row["function"], row["source_sha256"]))
    assert confirmed == {(r["function"], r["source_sha256"]) for r in report["runs"] if r["exact"]}
    assert len(used) == len(set(used)) == len(attempts) and set(used) == set(attempts)
    comparison = []
    for case in report["cases"]:
        parent, child = [r for r in report["runs"] if r["function"] == case["function"]]
        for key in ("initial_sha256", "target_sha256", "compiler_sha256", "assistance"):
            assert worlds[(case["function"], "control")]["context"][key] == worlds[(case["function"], "expanded")]["context"][key]
        comparison.append({**case, "gained": child["exact"] and not parent["exact"],
            "lost": parent["exact"] and not child["exact"], "control_exact": parent["exact"],
            "expanded_exact": child["exact"], "control_compiles": parent["compiles"], "expanded_compiles": child["compiles"]})
    assert comparison == report["comparison"]
    assert report["followup_gains"] == [r["function"] for r in comparison if r["phase"] == "followup" and r["gained"]]
    assert report["known_losses"] == [r["function"] for r in comparison if r["phase"] == "retention" and not r["expanded_exact"]]
    assert report["losses"] == [r["function"] for r in comparison if r["lost"]]
    assert report["total_compiles"] == len(attempts) == sum(r["compiles"] for r in report["runs"]) + len(confirmed)
    assert len(attempts) == report["ledger"]["spent"]["evaluation_compiles"]
    assert not report["ledger"]["overruns"]

    # Causal probes preceded the frozen generator; account for their failures too.
    probe_path = Path(report["database"]).parents[1] / "probe/attempts.sqlite"
    probe_db, probes, _, probe_edges = attempts_at(probe_path)
    first = json.loads((SHARED / "probe.json").read_text())["runs"]
    second = json.loads((SHARED / "probe-alias.json").read_text())
    assert len(probes) == len(first) + len(second)
    for row in first + second:
        stored = probes[row["receipt_id"]]
        bind(row, stored)
        label = row["label"].removeprefix("confirm-")
        assert digest((SHARED / f"probe-{label}.c").read_text()) == stored["source_sha256"]
    parameter = json.loads((SHARED / "parameter-probe.json").read_text())
    _, parameter_attempts, _, parameter_edges = attempts_at(parameter["database"])
    assert len(parameter_attempts) == len(parameter["runs"]) == 3
    for row in parameter["runs"]:
        stored = parameter_attempts[row["receipt_id"]]
        bind(row, stored)
        assert digest((SHARED / f"parameter-{row['label']}.c").read_text()) == stored["source_sha256"]
    summary = {"complete": True, "paired_compiles": len(attempts), "probe_compiles": len(probes),
        "parameter_probe_compiles": len(parameter_attempts),
        "total_compiles": len(attempts) + len(probes) + len(parameter_attempts),
        "explicit_parent_edges": len(edges) + len(probe_edges) + len(parameter_edges),
        "worlds": len(worlds), "independent_paired_confirmations": len(confirmed),
        "followup_gains": report["followup_gains"], "known_losses": report["known_losses"],
        "all_receipt_bindings_valid": True, "training_eligible": False}
    (OUT / "audit.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
