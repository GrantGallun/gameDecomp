"""End-to-end fire test of the staged compile chain through staged agentrepair.run (model off).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/compile-chain-20260915/validate_hook.py

Isolated workspaces, a private copy of the probe KB, campaign node sources read-only.
Pass: drawRaceSetupPlayerCountPrompt ends compiling with >=1 logged compile-chain attempt;
updateRacePlayerSurfaceContact runs the chain without error (it may stay non-compiling);
a function whose source already compiles never runs the chain.
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

assert Path(agentrepair.__file__).is_relative_to(OUT / "staged-code"), agentrepair.__file__
state = campaign_state.read(RUN / "campaign.json")
COMPILING = next(n for n, v in state["nodes"].items()
                 if v.get("status") == "pending" and (v.get("residual") or {}).get("compiled") is True
                 and (v.get("instruction_count") or 999) < 40)
CASES = [("drawRaceSetupPlayerCountPrompt", "compiles"), ("updateRacePlayerSurfaceContact", "runs"),
         (COMPILING, "no_chain")]


def run(function):
    native = Path(tempfile.mkdtemp(prefix="compile-chain-hook-"))
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
                                  include_header_context=True, resilient=True)
        with sqlite3.connect(db) as conn:
            logged = conn.execute("SELECT COUNT(*) FROM attempts WHERE strategy LIKE 'agentrepair-compile-chain:%'").fetchone()[0]
            compiled_chain = conn.execute("SELECT COUNT(*) FROM attempts WHERE strategy LIKE 'agentrepair-compile-chain:%' "
                                          "AND compiled=1").fetchone()[0]
        reports = [r for r in receipt.get("context_reports", []) if isinstance(r, dict) and r.get("kind") == "compile-chain"]
        result = receipt.get("result", {})
        return {"function": function, "improved": result.get("best_score_improved"),
                "best_compiled": (result.get("best_residual") or {}).get("compiled"),
                "best_score": (result.get("best_residual") or {}).get("weighted_progress_score"),
                "chain_reports": len(reports), "chain_compiled": [r.get("compiled") for r in reports],
                "logged_chain_attempts": logged, "logged_chain_compiled": compiled_chain,
                "chain_errors": [e for r in reports for e in r.get("log", []) if e.get("status") == "error"],
                "chain_log": [{k: e.get(k) for k in ("round", "fixes", "best", "progress", "stopped")}
                              for r in reports for e in r.get("log", [])],
                "chain_stderr_tail": [row[0][-400:] for row in sqlite3.connect(db).execute(
                    "SELECT compiler_stderr FROM attempts WHERE strategy LIKE 'agentrepair-compile-chain:%'")]}
    finally:
        shutil.rmtree(native, ignore_errors=True)


rows = []
ok = True
for function, expectation in CASES:
    row = run(function)
    row["expectation"] = expectation
    if expectation == "compiles":
        row["passed"] = bool(row["logged_chain_compiled"] >= 1 and row["improved"] and row["best_compiled"]
                             and not row["chain_errors"])
    elif expectation == "runs":
        row["passed"] = bool(row["chain_reports"] >= 1 and not row["chain_errors"])
    else:
        row["passed"] = row["chain_reports"] == 0 and row["logged_chain_attempts"] == 0
    ok &= row["passed"]
    rows.append(row)
    print(json.dumps(row), flush=True)
(OUT / "validate-hook.json").write_text(json.dumps({"passed": ok, "rows": rows}, indent=1))
print("PASSED" if ok else "FAILED")
