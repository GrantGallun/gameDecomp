"""End-to-end fire test of the staged hook: staged agentrepair.run with regalloc_budget on real functions.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/regalloc-deploy-20260913/validate_hook.py

Isolated workspace, private copy of the probe KB (never the live campaign DB), no
model calls. Pass: each function's result is object-exact through the normal
agentrepair path, and exactly one regalloc-search attempt is logged per function.
"""
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT / "staged-code"))
from eval import agentrepair, campaign_workers  # noqa: E402

ROOT = OUT.parents[2]
COHORT = {r["name"]: r for r in json.loads((ROOT / "eval/results/regalloc-20260913/cohort.json").read_text())["functions"]}
FUNCTIONS = ["updateRaceUiSparkle", "initRaceCourseTripleParticle", "reserveSoundEffectQueueReadIndex"]
assert Path(agentrepair.__file__).is_relative_to(OUT / "staged-code"), agentrepair.__file__

rows = []
for function in FUNCTIONS:
    native = Path(tempfile.mkdtemp(prefix="regalloc-hook-"))
    try:
        repo = campaign_workers.isolate(Path.home() / "decomp/sbk1", native / "game", function)
        db = native / "kb.sqlite"
        shutil.copy2(Path.home() / "decomp/kb-sbk1-parkedprobe-20260913.sqlite", db)
        row = COHORT[function]
        source = Path(row["source"]).read_text()
        receipt = agentrepair.run(repo=repo, db=db, function=function, source=source, source_parent_attempt_id=None,
                                  out=native / "receipt.json", best_source_out=native / "best.c", model="none",
                                  endpoint="http://127.0.0.1:9", draws=1, depth=4, beam=3, max_calls=0, timeout=10,
                                  think="low", num_thread=4, temperature=0.35, num_predict=100, seed=1, cache_dir=None,
                                  verbose=False, deterministic_budget=0, structured_output=True, retry_invalid=True,
                                  include_header_context=True, resilient=True, regalloc_budget=300)
        with sqlite3.connect(db) as conn:
            logged = conn.execute("SELECT COUNT(*) FROM attempts WHERE strategy='agentrepair-regalloc-search'").fetchone()[0]
        report = next((r for r in receipt.get("context_reports", []) if r.get("kind") == "regalloc-search"), None)
        rows.append({"function": function, "exact": receipt["result"]["exact"], "logged_regalloc_attempts": logged,
                     "search": {k: report.get(k) for k in ("exact", "best_label", "compiles")} if report else None})
    finally:
        shutil.rmtree(native, ignore_errors=True)
    print(json.dumps(rows[-1]), flush=True)
ok = all(r["exact"] and r["logged_regalloc_attempts"] == 1 for r in rows)
(OUT / "validate-hook.json").write_text(json.dumps({"passed": ok, "rows": rows}, indent=1))
print("PASSED" if ok else "FAILED")
