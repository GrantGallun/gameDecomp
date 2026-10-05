"""End-to-end fire test of the staged hook: staged agentrepair.run, regalloc_search profile settings, real functions.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/uopt-guided-20260915/validate_hook.py

Isolated workspace, private copy of the probe KB (never the live campaign DB), no model calls.
Pass: every function is object-exact through the normal agentrepair path with exactly one
logged regalloc-search attempt and trace guidance active (trace_calls >= 1); and the same run
with a wrong uopt pin stays unguided (trace_calls == 0).
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
from eval import completion_campaign as campaign  # noqa: E402

ROOT = OUT.parents[2]
COHORT = {r["name"]: r for r in json.loads((ROOT / "eval/results/regalloc-20260913/cohort.json").read_text())["functions"]}
FUNCTIONS = ["updateCharacterSelectRosterIcons", "updateRaceUiTrickScorePopupSlideIn", "updateRaceUiSparkle"]
assert Path(agentrepair.__file__).is_relative_to(OUT / "staged-code"), agentrepair.__file__
PROFILE = next(p for p in campaign.PROFILES if p["name"] == "regalloc_search")


def run(function, uopt_sha256):
    native = Path(tempfile.mkdtemp(prefix="uopt-guided-hook-"))
    try:
        repo = campaign_workers.isolate(Path.home() / "decomp/sbk1", native / "game", function)
        db = native / "kb.sqlite"
        shutil.copy2(Path.home() / "decomp/kb-sbk1-parkedprobe-20260913.sqlite", db)
        source = Path(COHORT[function]["source"]).read_text()
        receipt = agentrepair.run(repo=repo, db=db, function=function, source=source, source_parent_attempt_id=None,
                                  out=native / "receipt.json", best_source_out=native / "best.c", model="none",
                                  endpoint="http://127.0.0.1:9", draws=1, depth=4, beam=3, max_calls=0, timeout=10,
                                  think="low", num_thread=4, temperature=0.35, num_predict=100, seed=1, cache_dir=None,
                                  verbose=False, deterministic_budget=0, structured_output=True, retry_invalid=True,
                                  include_header_context=True, resilient=True,
                                  regalloc_budget=PROFILE["regalloc_budget"],
                                  regalloc_trace_cc=PROFILE["regalloc_trace_cc"],
                                  regalloc_trace_uopt_sha256=uopt_sha256)
        with sqlite3.connect(db) as conn:
            logged = conn.execute("SELECT COUNT(*) FROM attempts WHERE strategy='agentrepair-regalloc-search'").fetchone()[0]
        report = next((r for r in receipt.get("context_reports", []) if r.get("kind") == "regalloc-search"), None)
        leftover = (repo / "uoptlist").exists()
        return {"function": function, "pin": "matching" if uopt_sha256 == PROFILE["regalloc_trace_uopt_sha256"] else "wrong",
                "exact": receipt["result"]["exact"], "logged_regalloc_attempts": logged, "uoptlist_left_in_repo": leftover,
                "search": {k: report.get(k) for k in ("exact", "best_label", "compiles", "trace_calls", "first_decisions")}
                if report else None}
    finally:
        shutil.rmtree(native, ignore_errors=True)


rows = [run(f, PROFILE["regalloc_trace_uopt_sha256"]) for f in FUNCTIONS]
rows.append(run(FUNCTIONS[0], "0" * 64))
for row in rows:
    print(json.dumps(row), flush=True)
guided = [r for r in rows if r["pin"] == "matching"]
ok = (all(r["exact"] and r["logged_regalloc_attempts"] == 1 and r["search"] and r["search"]["trace_calls"] >= 1
          and not r["uoptlist_left_in_repo"] for r in guided)
      and all(r["search"] and r["search"]["trace_calls"] == 0 for r in rows if r["pin"] == "wrong"))
(OUT / "validate-hook.json").write_text(json.dumps({"passed": ok, "rows": rows}, indent=1))
print("PASSED" if ok else "FAILED")
