"""End-to-end check of the staged Panel path that builds its own callee environment (callee_environment=None).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/pointer-contracts-20260914/validate.py

Pass: func_8005EA4C and func_80064C68 (stack-offset-only failures live) are observed passes, the
panel report lists pointer-contract admissions, and initLaunchRampCourseObject (a passing control)
still passes.
"""
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT / "staged-code"))
from eval import campaign_state, campaign_workers, semantic_lane  # noqa: E402
from solver import modelrepair, pointer_contracts, workspace  # noqa: E402

assert Path(pointer_contracts.__file__).is_relative_to(OUT / "staged-code"), pointer_contracts.__file__
RUN = OUT.parents[0] / "resume-pipeline-20260908"
state = campaign_state.read(RUN / "campaign.json")
CASES = {"func_8005EA4C": "pass", "func_80064C68": "pass", "initLaunchRampCourseObject": "pass"}
rows = []
for function, expected in CASES.items():
    native = Path(tempfile.mkdtemp(prefix="pc-validate-"))
    try:
        repo = campaign_workers.isolate(Path.home() / "decomp/sbk1", native / "game", function)
        ws = repo / "nonmatchings" / function
        db = native / "kb.sqlite"
        shutil.copy2(Path.home() / "decomp/kb-sbk1-parkedprobe-20260913.sqlite", db)
        source = Path(state["nodes"][function]["source"]).read_text()
        with sqlite3.connect(db) as conn:
            att = workspace.score(ws, repo, function + "_validate", source, conn=conn, func=function)
        panel = semantic_lane.Panel(repo, ws, function, 64, 10000, 5000, None, source)
        result = panel(modelrepair.CandidateState(source, att, ws / (function + "_validate.o"))) or {}
        contracts = [row["callee"] for row in panel.report["callee_admission"] if row.get("status") == "pointer-contract"]
        row = {"function": function, "status": result.get("status"), "counts": result.get("counts"), "contracts": contracts}
        row["ok"] = result.get("status") in ("observed_pass", "observed_pass_with_execution_debt") and bool(contracts)
        rows.append(row)
    finally:
        shutil.rmtree(native, ignore_errors=True)
    print(json.dumps(rows[-1]), flush=True)
ok = all(r["ok"] for r in rows)
(OUT / "validate.json").write_text(json.dumps({"passed": ok, "rows": rows}, indent=1))
print("PASSED" if ok else "FAILED")
