"""The 28 never-compiled functions through the EXISTING deterministic compile chain (solver.compile_chain:
C89/linkage, do-while lowering, m2c placeholders, undefined identifiers), which the admission ladder
(solver.compile_fallback.admit) never calls. never_compiled_causes.out: the failures are m2c artifacts,
missing layouts and header conflicts -- the chain's territory, not open-ended code repair. The model
self-fix replay spent ~80 minutes on 2 functions without a compile and was stopped.

Each function's latest distinct intake drafts are chained from the original and from the admission
context/C89 form. No model, no stub baselines (a stub compiles but is not the function). Private KB.

    python3 never_compiled_chain.py      (WSL)
"""
import collections
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import campaign_workers  # noqa: E402
from solver import compile_chain, compile_fallback, placeholder_declarations, workspace  # noqa: E402

CAMPAIGN = Path("/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite")
REPO = Path("/home/grant/decomp/sbk1")
NATIVE = Path("/home/grant/decomp/experiments/admission-replay-20260927")
kb = sqlite3.connect(NATIVE / "replay.sqlite")
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
out = NATIVE / "never_compiled_chain.jsonl"
out.unlink(missing_ok=True)
rows = []
for name in targets:
    iso = campaign_workers.isolate(REPO, NATIVE / "repos" / name, name)
    ws = iso / "nonmatchings" / name
    count = [0]

    def score(label, code, _n=name, _ws=ws, _iso=iso):
        count[0] += 1
        return workspace.score(_ws, _iso, f"{_n}_chain_{count[0]}", code, conn=kb, func=_n,
                               strategy=f"never-compiled-chain:{label}", run_id="never-compiled-chain")
    row = {"function": name, "rescued": None, "tried": 0, "furthest": None}
    seen = set()
    for strategy, source in reversed(drafts[name][-6:]):
        if source in seen or row["rescued"]:
            continue
        seen.add(source)
        starts = [("original", source)]
        try:
            label = strategy.split(":", 1)[1] if ":" in strategy else strategy
            includes = compile_fallback.intake_includes(iso, name, workspace.target_asm(ws, name),
                                                        assisted=not label.startswith("binary-types:"))
            starts.append(("context", compile_fallback.with_context(compile_fallback.c89_repair(source, name),
                                                                    includes, iso)))
        except Exception:
            pass
        for start_label, code in starts:
            if row["rescued"] or not code:
                continue
            try:
                base = score(start_label, code)
                row["tried"] += 1
                if base.compiled:
                    row["rescued"] = {"by": start_label, "score": base.score}
                    break
                headers = placeholder_declarations.header_names(iso, code)
                chained, log = compile_chain.chain(name, start_label, code, base, score, headers=headers)
                steps = [entry.get("fix") or entry.get("stage") for entry in log]
                for step_label, _code, att in chained:
                    if att.compiled:
                        row["rescued"] = {"by": f"{start_label}+chain", "steps": steps, "score": att.score,
                                          "exact": bool(att.exact)}
                        break
                last = chained[-1][2] if chained else base
                row["furthest"] = {"from": start_label, "steps": steps,
                                   "first_error": (compile_chain.first_error(last) or (None, ""))[1][:120]}
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"[:200]
    kb.commit()
    rows.append(row)
    with out.open("a") as stream:
        stream.write(json.dumps(row) + "\n")
    print(json.dumps({"function": name, "rescued": row["rescued"]}), flush=True)
print(json.dumps({"functions": len(rows), "rescued": sum(1 for r in rows if r["rescued"]),
                  "by": collections.Counter((r["rescued"] or {}).get("by") for r in rows),
                  "errors": sum(1 for r in rows if r.get("error"))}, indent=2))
