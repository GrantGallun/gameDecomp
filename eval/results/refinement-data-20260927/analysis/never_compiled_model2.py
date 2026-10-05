"""Can the model compile what the deterministic ladder cannot? Capped, informed rerun on the functions where
nothing ever compiled (2026-09-28).

The first model replay spent ~80 min on 2 functions without a compile: calls took 2-8 min because Ollama
reloaded the model 60 times (adaptive 16K/32K num_ctx), every draft of a function was retried, and the
model never saw the struct layouts most failures are about (never_compiled_causes.out). This run:
  - pinned context (SOLVER_FIXED_CONTEXT=32768, set by the caller);
  - the ladder with the compile-chain rung, so the model starts where the chain got furthest;
  - at most two drafts per function, and only drafts whose first compiler error differs;
  - the target assembly as `facts` (loads/stores state field offsets and widths);
  - 3 model rounds per draft, patience 2.
Private KB; campaign untouched.

    SOLVER_FIXED_CONTEXT=32768 python3 never_compiled_model2.py SHARD NSHARDS      (WSL)
"""
import collections
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import campaign_workers  # noqa: E402
from eval.redraft_pilot import extract_source, generation_seconds  # noqa: E402
from solver import compile_fallback, llm, workspace  # noqa: E402

SHARD, NSHARDS = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (0, 1)
CAMPAIGN = Path("/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite")
REPO = Path("/home/grant/decomp/sbk1")
NATIVE = Path("/home/grant/decomp/experiments/admission-replay-20260927")
ASM_LIMIT = 12000                               # characters; keeps prompt + 8K answer inside the 32K pin
kb = sqlite3.connect(NATIVE / "replay.sqlite", timeout=120)
camp = sqlite3.connect(f"file:{CAMPAIGN}?mode=ro", uri=True)

ok_any = collections.Counter()
drafts = collections.defaultdict(list)
for name, strategy, ok, source, err in camp.execute(
        "select f.name, a.strategy, coalesce(a.compiled,0), a.source_code, coalesce(a.compiler_stderr,'') "
        "from attempts a join functions f on f.addr=a.func_addr order by a.id"):
    ok_any[name] += ok
    if (strategy or "").startswith("campaign-intake") and not ok and source:
        drafts[name].append((strategy, source, err))
targets = sorted(n for n in drafts if not ok_any[n])[SHARD::NSHARDS]


def signature(err: str) -> str:
    first = next((l for l in err.splitlines() if "Error" in l), err[:80])
    return re.sub(r"line \d+|'[^']*'", "_", first)[:100]


out = NATIVE / f"never_compiled_model2.{SHARD}.jsonl"
for name in targets:
    iso = campaign_workers.isolate(REPO, NATIVE / "repos" / name, name)
    ws = iso / "nonmatchings" / name
    count, calls = [0], []

    def compile_fn(rung, code, _n=name, _ws=ws, _iso=iso):
        count[0] += 1
        return workspace.score(_ws, _iso, f"{_n}_m2_{count[0]}", code, conn=kb, func=_n,
                               strategy=f"never-compiled-model2:{rung}", run_id="never-compiled-model2")

    def fix_fn(prompt, _iso=iso):
        started = time.monotonic()
        try:
            text, meta = llm.generate(llm.host(), "gpt-oss:20b", prompt, timeout=900,
                                      think="medium", temperature=0.2, num_predict=8000, seed=1)
            wall = time.monotonic() - started
            calls.append({"wall": round(wall, 1), "tokens": (meta or {}).get("eval_count")})
            return (extract_source(text, lambda inc: (_iso / "include" / inc).is_file()),
                    generation_seconds(meta, wall))
        except Exception as exc:
            calls.append({"wall": round(time.monotonic() - started, 1), "error": type(exc).__name__})
            return "", time.monotonic() - started

    asm = workspace.target_asm(ws, name) or ""
    facts = ("TARGET ASSEMBLY (what this C must compile to; each load/store gives a field's offset and "
             "width, e.g. `lw v0,0x24(a0)` = 4-byte field at 0x24 of the struct a0 points to):\n```\n"
             + asm[:ASM_LIMIT] + ("\n... (truncated)" if len(asm) > ASM_LIMIT else "") + "\n```")
    chosen, seen = [], set()
    for strategy, source, err in reversed(drafts[name]):      # newest first, distinct error signatures
        sig = signature(err)
        if sig not in seen and len(chosen) < 2:
            seen.add(sig)
            chosen.append((strategy, source))
    row = {"function": name, "drafts_total": len(drafts[name]), "drafts_tried": len(chosen), "rescued": None}
    started = time.monotonic()
    for strategy, source in chosen:
        try:
            base = compile_fn("original", source)
            label = strategy.split(":", 1)[1] if ":" in strategy else strategy
            includes = compile_fallback.intake_includes(iso, name, asm,
                                                        assisted=not label.startswith("binary-types:"))
            res = compile_fallback.admit(source, base, function=name, compile_fn=compile_fn, repo=iso,
                                         context_includes=includes, fix_fn=fix_fn,
                                         max_fix_rounds=3, patience=2, facts=facts)
            if res.attempt is not None:
                row["rescued"] = {"by": res.rescued_by, "score": res.attempt.score,
                                  "exact": bool(res.attempt.exact), "model_seconds": round(res.model_seconds, 1)}
                break
            row.setdefault("last_steps", [s.get("step") for s in res.steps])
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"[:200]
    row.update(model_calls=calls, seconds=round(time.monotonic() - started, 1), compiles=count[0])
    kb.commit()
    with out.open("a") as stream:
        stream.write(json.dumps(row) + "\n")
    print(json.dumps({"function": name, "rescued": row["rescued"], "calls": len(calls),
                      "seconds": row["seconds"]}), flush=True)
