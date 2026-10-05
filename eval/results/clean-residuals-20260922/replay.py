"""Replay the corrected member and cast repairs on the same 200 incumbents."""
import hashlib
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval import campaign_workers
from eval.intake_probe import SEQUENCE, rank, classify_residual
from eval.intake_runners import RUNNERS
from eval.tool_agent_run import _attempt_to_verdict
from solver import frontend_diagnostics, workspace

FINAL = "--final" in sys.argv
OUT = Path(__file__).resolve().parent
RECEIPT = "paired-final.json" if FINAL else "paired.json"
DB_NAME = "final-attempts.sqlite" if FINAL else "paired-attempts.sqlite"
BUILD_NAME = "final-builds" if FINAL else "paired-builds"
SOURCE_NAME = "final.c" if FINAL else "after.c"
FRONT_NAME = "final-frontend.json" if FINAL else "after-frontend.json"
REPO = Path.home() / "decomp/sbk1"
NATIVE = Path.home() / "decomp/experiments/clean-residuals-20260922"
NATIVE.mkdir(parents=True, exist_ok=True)
readonly = sqlite3.connect(f"file:{Path.home() / 'decomp/kb-sbk1.sqlite'}?mode=ro", uri=True)
prior_exacts = {r[0] for r in readonly.execute('SELECT DISTINCT f.name FROM attempts a JOIN functions f ON f.addr=a.func_addr WHERE a.exact=1')}
conn = sqlite3.connect(NATIVE / DB_NAME)
conn.executescript((ROOT / "kb/schema.sql").read_text())
for table in ("tus", "functions"):
    columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    values = readonly.execute(f"SELECT {','.join(columns)} FROM {table}").fetchall()
    conn.executemany(f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", values)
conn.commit()
frame = json.loads((OUT / "census.json").read_text())
stages = SEQUENCE[SEQUENCE.index('eval.intake_runners.void_members'):]
assert stages == ('eval.intake_runners.void_members', 'eval.intake_runners.frontend_casts', 'eval.intake_runners.ido_byte_cursors')
code = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for directory in ("solver", "eval", "kb", "oracle") for p in (ROOT / directory).glob("*.py")}
rows = []
started = time.monotonic()
for entry in frame["rows"]:
    name = entry["function"]
    folder = OUT / "states" / name
    source = (folder / "before.c").read_text()
    assert hashlib.sha256(source.encode()).hexdigest() == entry["source_sha256"]
    native = campaign_workers.isolate(REPO, NATIVE / BUILD_NAME / name, name)
    ws = native / "nonmatchings" / name
    def score(candidate, stage):
        attempt = workspace.score(ws, native, name, candidate, conn=conn, func=name,
            strategy=f"clean-residuals:paired:{stage}", model="zero-model", run_id="clean-residuals-paired-20260922")
        return _attempt_to_verdict(attempt)
    def observe(candidate, verdict):
        frontend = frontend_diagnostics.analyse(candidate, repo=native, target=entry["target"])
        return {"compiled": verdict["compiled"], "exact": verdict["exact"], "score": verdict["score"],
                "frontend": frontend["status"], "errors": frontend["error_count"],
                "classes": dict(Counter(classify_residual(e["what"]) for e in frontend["errors"])),
                "source_sha256": hashlib.sha256(candidate.encode()).hexdigest()}, frontend
    best = score(source, "baseline")
    before, before_frontend = observe(source, best)
    assert (before["compiled"], before["exact"], before["errors"]) == (entry["compiled"], entry["exact"], entry["errors"]), name
    current = source
    trace = []
    for label in stages:
        context = dict(function=name, candidate=current, repo=str(native), target=entry["target"],
                       workspace=str(ws), target_asm_path=str(ws / "target.s"), initial_verdict=best)
        result = RUNNERS[label](context, {})
        step = {"action": label, "changed": result.get("changed", False),
                "detail": result.get("detail"), "reason": result.get("reason")}
        if result.get("changed"):
            candidate = result["source"]
            verdict = score(candidate, label.rsplit('.', 1)[-1])
            adopted = rank(verdict) >= rank(best)
            step.update(adopted=adopted, compiled=verdict["compiled"], exact=verdict["exact"],
                        source_sha256=hashlib.sha256(candidate.encode()).hexdigest())
            if adopted:
                current, best = candidate, verdict
        trace.append(step)
    after, after_frontend = observe(current, best)
    (folder / SOURCE_NAME).write_text(current)
    (folder / FRONT_NAME).write_text(json.dumps(after_frontend, indent=2) + "\n")
    row = {"function": name, "before": before, "after": after, "trace": trace,
           "already_exact_in_research_kb": name in prior_exacts}
    rows.append(row)
    report = {"rows": rows, "expected": 200, "seconds": time.monotonic() - started,
              "code_sha256": code, "attempt_db": str(NATIVE / DB_NAME),
              "calls": conn.execute('SELECT count(*) FROM attempts').fetchone()[0]}
    (OUT / RECEIPT).write_text(json.dumps(report, indent=2) + "\n")
    if len(rows) % 20 == 0 or before['compiled'] != after['compiled'] or before['exact'] != after['exact']:
        print(json.dumps({"n": len(rows), "function": name,
            "before": [before[k] for k in ('compiled','frontend','exact','errors')],
            "after": [after[k] for k in ('compiled','frontend','exact','errors')]}), flush=True)
conn.close()
