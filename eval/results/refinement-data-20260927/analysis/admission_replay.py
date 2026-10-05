"""Replay the admission choke point on REAL failed intake drafts from the campaign.

For a seeded sample of functions, take the latest uncompiled `campaign-intake:*` draft, compile it
in an isolated workspace, and run exactly what the wired intake runs
(`compile_fallback.admit` with `intake_includes`). Report the rescue rate by rung and whether a
rescued draft scores above the best draft that DID compile at intake for that function (a new
starting point, not just a duplicate). Nothing is written to the campaign; attempts go to a
private slim KB.

    python3 admission_replay.py [N]      (WSL; run from the repo root)
"""
import collections
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import campaign_workers  # noqa: E402
from eval.redraft_pilot import slim_kb  # noqa: E402
from solver import compile_fallback, workspace  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 150
CAMPAIGN = Path("/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite")
REPO = Path("/home/grant/decomp/sbk1")
NATIVE = Path("/home/grant/decomp/experiments/admission-replay-20260927")
NATIVE.mkdir(parents=True, exist_ok=True)
kb_path = NATIVE / "replay.sqlite"
if not kb_path.exists():
    slim_kb(CAMPAIGN, kb_path)
kb = sqlite3.connect(kb_path)
camp = sqlite3.connect(f"file:{CAMPAIGN}?mode=ro", uri=True)

latest = {}
for aid, name, strategy, source in camp.execute(
        "select a.id, f.name, a.strategy, a.source_code from attempts a join functions f "
        "on f.addr=a.func_addr where a.strategy like 'campaign-intake:%' and coalesce(a.compiled,0)=0 "
        "and a.source_code is not null order by a.id"):
    latest[name] = (aid, strategy, source)
best_compiled = dict(camp.execute(
    "select f.name, max(a.score) from attempts a join functions f on f.addr=a.func_addr "
    "where a.strategy like 'campaign-intake%' and a.compiled=1 group by f.name"))
names = sorted(latest, key=lambda n: hashlib.sha256(f"20260927:{n}".encode()).hexdigest())[:N]

out = NATIVE / "replay.jsonl"
done = {json.loads(l)["function"] for l in out.read_text().splitlines()} if out.exists() else set()
for name in names:
    if name in done:
        continue
    aid, strategy, source = latest[name]
    row = {"function": name, "draft_attempt": aid, "strategy": strategy}
    try:
        iso = campaign_workers.isolate(REPO, NATIVE / "repos" / name, name)
        ws = iso / "nonmatchings" / name
        label = strategy.split(":", 1)[1] if ":" in strategy else strategy
        tags = []

        def compile_fn(rung, code):
            tags.append(rung)
            return workspace.score(ws, iso, f"{name}_replay_{len(tags)}", code, conn=kb, func=name,
                                   strategy=f"admission-replay:{rung}", run_id="admission-replay")
        base = compile_fn("original", source)
        row["original_compiles_here"] = bool(base.compiled)
        if not base.compiled:
            includes = compile_fallback.intake_includes(
                iso, name, workspace.target_asm(ws, name),
                assisted=not label.startswith("binary-types:"))
            res = compile_fallback.admit(source, base, function=name, compile_fn=compile_fn,
                                         repo=iso, context_includes=includes)
            row.update(rescued_by=res.rescued_by,
                       score=res.attempt.score if res.attempt is not None else None,
                       best_compiled_at_intake=best_compiled.get(name),
                       steps=[s.get("step") for s in res.steps])
        row["status"] = "done"
    except Exception as exc:
        row.update(status="error", error=f"{type(exc).__name__}: {exc}"[:300])
    kb.commit()
    with out.open("a") as stream:
        stream.write(json.dumps(row) + "\n")

rows = [json.loads(l) for l in out.read_text().splitlines()]
done_rows = [r for r in rows if r["status"] == "done" and not r.get("original_compiles_here")]
by = collections.Counter(r.get("rescued_by") for r in done_rows)
better = [r for r in done_rows if r.get("score") is not None and
          (r["best_compiled_at_intake"] is None or r["score"] > r["best_compiled_at_intake"])]
print(json.dumps({
    "sampled": len(rows), "errors": sum(r["status"] == "error" for r in rows),
    "compiled_here_without_help (environment drift since the campaign)":
        sum(bool(r.get("original_compiles_here")) for r in rows),
    "failed_drafts": len(done_rows), "rescued_by": dict(by),
    "rescue_rate": round(1 - by.get(None, 0) / max(len(done_rows), 1), 3),
    "rescued_and_above_best_compiled_intake_draft": len(better),
    "functions_with_no_compiled_intake_draft_now_rescued": sum(
        1 for r in better if r["best_compiled_at_intake"] is None)}, indent=2))
