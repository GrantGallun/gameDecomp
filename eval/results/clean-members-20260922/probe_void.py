"""Measure the existing void-member proposer on frozen post-header states."""
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval import campaign_workers
from eval.intake_probe import classify_residual
from eval.tool_agent_run import _attempt_to_verdict
from solver import frontend_diagnostics, void_field_repair, workspace

OUT = Path(__file__).resolve().parent
REPO = Path.home() / "decomp/sbk1"
NATIVE = Path.home() / "decomp/experiments/clean-members-20260922"
NATIVE.mkdir(parents=True, exist_ok=True)
readonly = sqlite3.connect(f"file:{Path.home() / 'decomp/kb-sbk1.sqlite'}?mode=ro", uri=True)
conn = sqlite3.connect(NATIVE / "attempts.sqlite")
conn.executescript((ROOT / "kb/schema.sql").read_text())
for table in ("tus", "functions"):
    columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    values = readonly.execute(f"SELECT {','.join(columns)} FROM {table}").fetchall()
    conn.executemany(f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", values)
conn.commit()
frame = json.loads((OUT / "census.json").read_text())
results = []
started = time.monotonic()
for entry in frame["rows"]:
    name = entry["function"]
    folder = OUT / "states" / name
    source = (folder / "before.c").read_text()
    front = json.loads((folder / "frontend.json").read_text())
    if not any("base type 'void'" in e["what"] for e in front["errors"]):
        continue
    assembly = (REPO / "nonmatchings" / name / "target.s").read_text()
    # Reconstruct complete source-bound diagnostic lines from the structured
    # checker result; stored human-readable diagnostics may be windowed.
    lines = source.splitlines()
    diagnostics = "\n".join(
        f"candidate.c:{e['line']}:{e['column']}: error: {e['what']}\n {e['line']} | {lines[e['line']-1]}"
        for e in front["errors"] if 1 <= e["line"] <= len(lines))
    proposal = void_field_repair.propose(source, name, assembly, diagnostics)
    row = {"function": name, "changes": proposal["changes"], "compiled_before": entry["compiled"],
           "frontend_before": entry["frontend"], "errors_before": entry["errors"]}
    if proposal["source"] != source:
        native = campaign_workers.isolate(REPO, NATIVE / "builds" / name, name)
        ws = native / "nonmatchings" / name
        verdicts = {}
        for arm, candidate in (("before", source), ("after", proposal["source"])):
            attempt = workspace.score(ws, native, name, candidate, conn=conn, func=name,
                strategy=f"clean-members:void-probe:{arm}", model="zero-model", run_id="clean-members-20260922")
            verdict = _attempt_to_verdict(attempt)
            check = frontend_diagnostics.analyse(candidate, repo=native, target=entry["target"])
            verdicts[arm] = {k: verdict.get(k) for k in ("compiled", "exact", "score", "stderr", "diff")}
            verdicts[arm].update(frontend=check["status"], errors=check["error_count"],
                                 source_sha256=hashlib.sha256(candidate.encode()).hexdigest())
            (folder / f"void-{arm}-frontend.json").write_text(json.dumps(check, indent=2) + "\n")
        (folder / "void-after.c").write_text(proposal["source"])
        row["verdicts"] = verdicts
        print(json.dumps({"function": name, "changes": len(proposal["changes"]),
                          "before": [verdicts["before"][k] for k in ("compiled","frontend","exact","errors")],
                          "after": [verdicts["after"][k] for k in ("compiled","frontend","exact","errors")]}), flush=True)
    results.append(row)
    (OUT / "void-probe.json").write_text(json.dumps({"rows": results, "seconds": time.monotonic()-started,
                                                    "attempt_db": str(NATIVE / "attempts.sqlite")}, indent=2) + "\n")
print(json.dumps({"states": len(results), "fired": sum("verdicts" in r for r in results)}, indent=2))
