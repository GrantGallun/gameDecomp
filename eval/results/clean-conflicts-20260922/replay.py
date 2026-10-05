"""Paired deterministic intake replay; native isolated builds, private attempt log."""
import hashlib
import inspect
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval import campaign_workers
from eval.dev_set_export import recover_source
from eval.intake_probe import SEQUENCE, gated, rank, classify_residual
from eval.intake_runners import RUNNERS
from eval.tool_agent_run import _attempt_to_verdict
from solver import compile_recovery, frontend_diagnostics, workspace

OUT = Path(__file__).resolve().parent
REPO = Path.home() / "decomp/sbk1"
NATIVE = Path.home() / "decomp/experiments/clean-conflicts-20260922"
NATIVE.mkdir(parents=True, exist_ok=True)
frame = json.loads((ROOT / "eval/results/intake-20260921/wide-intake-clean.json").read_text())
readonly = sqlite3.connect(f"file:{Path.home() / 'decomp/kb-sbk1.sqlite'}?mode=ro", uri=True)
conn = sqlite3.connect(NATIVE / "attempts.sqlite")
conn.executescript((ROOT / "kb/schema.sql").read_text())
for table in ("tus", "functions"):
    columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    values = readonly.execute(f"SELECT {','.join(columns)} FROM {table}").fetchall()
    conn.executemany(f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", values)
conn.commit()

# The archived function preserves the pre-task working tree, including earlier
# agents' uncommitted changes. HEAD is not the baseline for this experiment.
new_function = compile_recovery.header_variant
old_text = (OUT / "header-before.py.txt").read_text()
namespace = dict(compile_recovery.__dict__)
exec(compile(old_text, "pre-fix-header-variant", "exec"), namespace)
old_function = namespace["header_variant"]
(OUT / "header-after.py.txt").write_text(inspect.getsource(new_function))
code = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for directory in ("solver", "eval", "kb", "oracle") for p in (ROOT / directory).glob("*.py")}
rows = []
started = time.monotonic()
limit = int(sys.argv[1]) if len(sys.argv) > 1 else 200
for entry in frame["rows"][:limit]:
    name = entry["function"]
    draft, lineage = recover_source(readonly, entry["draft_sha256"])
    if draft is None:
        raise RuntimeError((name, lineage))
    native = campaign_workers.isolate(REPO, NATIVE / "builds" / name, name)
    ws = native / "nonmatchings" / name
    target = readonly.execute("SELECT t.name FROM functions f JOIN tus t ON t.id=f.tu_id WHERE f.name=?", (name,)).fetchone()[0]
    cache, frontends = {}, {}

    def score(source, arm, stage):
        digest = hashlib.sha256(source.encode()).hexdigest()
        if digest not in cache:
            attempt = workspace.score(ws, native, name, source, conn=conn, func=name,
                strategy=f"clean-conflicts:{arm}:{stage}", model="zero-model",
                run_id="clean-conflicts-20260922")
            cache[digest] = _attempt_to_verdict(attempt)
        return cache[digest]

    def frontend(source):
        digest = hashlib.sha256(source.encode()).hexdigest()
        if digest not in frontends:
            frontends[digest] = frontend_diagnostics.analyse(source, repo=native, target=target)
        return frontends[digest]

    initial = score(draft, "shared", "draft")
    context = dict(function=name, repo=str(native), target=target, workspace=str(ws),
                   target_asm_path=str(ws / "target.s"), kb_conn=readonly, widths={})
    row = {"function": name, "draft_sha256": entry["draft_sha256"], "arms": {}}
    for arm, implementation in (("before", old_function), ("after", new_function)):
        compile_recovery.header_variant = implementation
        current, best = draft, initial
        trace = []
        for label in SEQUENCE:
            # The target's reference .c declarations are excluded from BOTH arms.
            if label.endswith("source_type_declarations"):
                continue
            if not gated(label, best.get("stderr", "")):
                continue
            result = RUNNERS[label]({**context, "candidate": current, "initial_verdict": best}, {})
            if not result.get("changed"):
                continue
            child = result["source"]
            verdict = score(child, arm, label.rsplit(".", 1)[-1])
            adopted = rank(verdict) >= rank(best)
            trace.append({"action": label, "adopted": adopted, "detail": result.get("detail"),
                          "sha256": hashlib.sha256(child.encode()).hexdigest(),
                          "compiled": verdict["compiled"], "exact": verdict["exact"]})
            if adopted:
                current, best = child, verdict
        front = frontend(current)
        folder = OUT / "replay" / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{arm}.c").write_text(current)
        (folder / f"{arm}-frontend.json").write_text(json.dumps(front, indent=2) + "\n")
        row["arms"][arm] = {"compiled": best["compiled"], "exact": best["exact"], "score": best["score"],
            "frontend": front["status"], "errors": front["error_count"],
            "classes": dict(Counter(classify_residual(e["what"]) for e in front["errors"])),
            "source_sha256": hashlib.sha256(current.encode()).hexdigest(), "trace": trace}
    row["before_reproduces_saved_final"] = row["arms"]["before"]["source_sha256"] == entry["sequence"]["final_sha256"]
    row["unique_compiles"] = len(cache)
    rows.append(row)
    payload = {"rows": rows, "code_sha256": code, "expected": limit, "seconds": time.monotonic() - started,
               "attempt_db": str(NATIVE / "attempts.sqlite"), "reference_source_step": "excluded in both arms",
               "assistance": "assembly-only drafts; existing header-assisted intake; exposed development frame"}
    (OUT / "paired.json").write_text(json.dumps(payload, indent=2) + "\n")
    a, b = row["arms"]["before"], row["arms"]["after"]
    print(json.dumps({"n": len(rows), "function": name,
        "before": [a["compiled"], a["frontend"], a["exact"], a["errors"]],
        "after": [b["compiled"], b["frontend"], b["exact"], b["errors"]],
        "compiles": len(cache), "reproduced": row["before_reproduces_saved_final"]}), flush=True)
compile_recovery.header_variant = new_function
conn.close()
