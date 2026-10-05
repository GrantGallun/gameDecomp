"""End-to-end fire test of the staged enabling roots through staged agentrepair.run (model off, regalloc budget 300).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/const-store-20260915/validate_hook.py

Isolated workspaces, a private copy of the probe KB, campaign node sources read-only.
Pass: updateRacePlayerMode18AerialTrick ends object-exact with a regalloc-search report whose log has the
const_store_local root (depth 0); a register-dominant function with no enabling edit gets a report with no depth-0 row.
"""
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

OUT = Path(__file__).resolve().parent
RUN = OUT.parents[0] / "resume-pipeline-20260908"
sys.path.insert(0, str(OUT / "staged-code"))
from eval import agentrepair, campaign_state, campaign_workers  # noqa: E402
from solver import regalloc_mutations, regalloc_search  # noqa: E402

assert Path(agentrepair.__file__).is_relative_to(OUT / "staged-code"), agentrepair.__file__
state = campaign_state.read(RUN / "campaign.json")


def plain_register_node():
    for name, node in sorted(state["nodes"].items(), key=lambda kv: kv[1].get("instruction_count") or 999):
        faults = (node.get("residual") or {}).get("faults") or {}
        if node.get("status") != "pending" or not regalloc_search.register_dominant(faults, max_other=2):
            continue
        source = Path(node["source"]).read_text()
        if not list(regalloc_mutations.enabling_variants(source, name)):
            return name
    raise SystemExit("no plain register-dominant node")


CASES = [("updateRacePlayerMode18AerialTrick", "exact_via_root"), (plain_register_node(), "no_root")]


def run(function):
    native = Path(tempfile.mkdtemp(prefix="enabling-roots-hook-"))
    try:
        repo = campaign_workers.isolate(Path.home() / "decomp/sbk1", native / "game", function)
        db = native / "kb.sqlite"
        shutil.copy2(Path.home() / "decomp/kb-sbk1-parkedprobe-20260913.sqlite", db)
        source = Path(state["nodes"][function]["source"]).read_text()
        receipt = agentrepair.run(repo=repo, db=db, function=function, source=source, source_parent_attempt_id=None,
                                  out=native / "receipt.json", best_source_out=native / "best.c", model="none",
                                  endpoint="http://127.0.0.1:9", draws=1, depth=4, beam=3, max_calls=0, timeout=10,
                                  think="low", num_thread=4, temperature=0.35, num_predict=100, seed=1, cache_dir=None,
                                  verbose=False, deterministic_budget=0, structured_output=True, retry_invalid=True,
                                  include_header_context=True, resilient=True, regalloc_budget=300)
        reports = [r for r in receipt.get("context_reports", []) if isinstance(r, dict) and r.get("kind") == "regalloc-search"]
        result = receipt.get("result", {})
        return {"function": function, "exact": bool(result.get("exact")), "reports": len(reports),
                "search": [{k: r.get(k) for k in ("exact", "best_label", "compiles", "best_gradient")} for r in reports],
                "root_rows": [row for r in reports for row in r.get("log_tail", []) if row.get("depth") == 0],
                "log_tail_parents": sorted({row.get("parent") for r in reports for row in r.get("log_tail", [])})}
    finally:
        shutil.rmtree(native, ignore_errors=True)


rows, ok = [], True
for function, expectation in CASES:
    row = run(function)
    row["expectation"] = expectation
    if expectation == "exact_via_root":
        # log_tail keeps the last 20 rows; the exact child's parent names the root
        row["passed"] = bool(row["exact"] and row["search"] and row["search"][0]["exact"]
                             and any(p.startswith("const_store_local:") for p in row["log_tail_parents"]))
    else:
        row["passed"] = bool(row["reports"] == 1 and not row["root_rows"]
                             and not any(str(p).startswith("const_store_local:") for p in row["log_tail_parents"]))
    ok &= row["passed"]
    rows.append(row)
    print(json.dumps(row), flush=True)
(OUT / "validate-hook.json").write_text(json.dumps({"passed": ok, "rows": rows}, indent=1))
print("PASSED" if ok else "FAILED")
