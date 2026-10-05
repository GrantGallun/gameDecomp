"""Admission on the 28 functions where NOTHING ever compiled (intake_losses.out). Deterministic
rungs now; ``--model`` adds the convergent self-fix (run it when the GPU is free). Every failed
intake draft of each function is tried, best rescue kept. Private KB; campaign untouched.

    python3 never_compiled_replay.py [--model]     (WSL, repo root)
"""
import collections
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import campaign_workers  # noqa: E402
from eval.redraft_pilot import extract_source, generation_seconds, slim_kb  # noqa: E402
from solver import compile_fallback, llm, workspace  # noqa: E402

USE_MODEL = "--model" in sys.argv
CAMPAIGN = Path("/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite")
REPO = Path("/home/grant/decomp/sbk1")
NATIVE = Path("/home/grant/decomp/experiments/admission-replay-20260927")
kb_path = NATIVE / "replay.sqlite"
if not kb_path.exists():
    slim_kb(CAMPAIGN, kb_path)
kb = sqlite3.connect(kb_path)
camp = sqlite3.connect(f"file:{CAMPAIGN}?mode=ro", uri=True)

ok_any = collections.Counter()
drafts = collections.defaultdict(list)
for name, strategy, ok, source in camp.execute(
        "select f.name, a.strategy, coalesce(a.compiled,0), a.source_code from attempts a "
        "join functions f on f.addr=a.func_addr order by a.id"):
    ok_any[name] += ok
    if (strategy or "").startswith("campaign-intake") and not ok and source:
        drafts[name].append((strategy, source))
targets = sorted(n for n in drafts if not ok_any[n])
out = NATIVE / ("never_compiled_model.jsonl" if USE_MODEL else "never_compiled_det.jsonl")
rows = []
for name in targets:
    iso = campaign_workers.isolate(REPO, NATIVE / "repos" / name, name)
    ws = iso / "nonmatchings" / name
    count = [0]

    def compile_fn(rung, code, _n=name, _ws=ws, _iso=iso):
        count[0] += 1
        return workspace.score(_ws, _iso, f"{_n}_never_{count[0]}", code, conn=kb, func=_n,
                               strategy=f"never-compiled-replay:{rung}", run_id="never-compiled")

    def fix_fn(prompt, _iso=iso):
        started = time.monotonic()
        try:
            text, meta = llm.generate(llm.host(), "gpt-oss:20b", prompt, timeout=900,
                                      think="medium", temperature=0.2, num_predict=8000, seed=1)
            return (extract_source(text, lambda inc: (_iso / "include" / inc).is_file()),
                    generation_seconds(meta, time.monotonic() - started))
        except Exception:
            return "", time.monotonic() - started
    row = {"function": name, "drafts": len(drafts[name]), "rescued": None}
    seen = set()
    for strategy, source in drafts[name][-6:]:          # the latest distinct drafts
        if source in seen:
            continue
        seen.add(source)
        try:
            base = compile_fn("original", source)
            if base.compiled:
                row["rescued"] = {"by": "compiles-now (environment drift)", "score": base.score}
                break
            label = strategy.split(":", 1)[1] if ":" in strategy else strategy
            includes = compile_fallback.intake_includes(iso, name, workspace.target_asm(ws, name),
                                                        assisted=not label.startswith("binary-types:"))
            res = compile_fallback.admit(source, base, function=name, compile_fn=compile_fn,
                                         repo=iso, context_includes=includes,
                                         fix_fn=fix_fn if USE_MODEL else None)
            if res.attempt is not None:
                row["rescued"] = {"by": res.rescued_by, "score": res.attempt.score,
                                  "model_seconds": round(res.model_seconds, 1)}
                break
            row.setdefault("last_steps", [s.get("step") for s in res.steps])
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"[:200]
    kb.commit()
    rows.append(row)
    with out.open("a") as stream:
        stream.write(json.dumps(row) + "\n")
print(json.dumps({"functions": len(rows), "rescued": sum(1 for r in rows if r["rescued"]),
                  "by": collections.Counter((r["rescued"] or {}).get("by") for r in rows),
                  "errors": sum(1 for r in rows if r.get("error"))}, indent=2, default=str))
