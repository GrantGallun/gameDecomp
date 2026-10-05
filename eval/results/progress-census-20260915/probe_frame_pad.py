"""Does stack_layout's own `stack_drop_unused` fix the framePad functions? One compile per proposal kind.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/progress-census-20260915/probe_frame_pad.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

from eval import regalloc_probe  # noqa: E402
from solver import stack_layout, workspace  # noqa: E402

pending = {r["function"]: r for r in json.loads((HERE / "pending.json").read_text())}
names = sys.argv[1:] or ["osSetThreadPri", "osStopThread", "osViBlack", "osViSetSpecialFeatures", "osYieldThread",
                         "__osResetGlobalIntMask", "__osSetGlobalIntMask", "osDestroyThread"]
for name in names:
    entry = json.loads((HERE / "diffs" / f"{name}.json").read_text())
    row = pending[name]
    source = entry["source_text"]
    proposals = list(stack_layout.variants(source, name, entry["diff"]))
    kinds = [label for label, _k, _t in proposals]
    bench = regalloc_probe.Bench({"name": name, "source": row["source"]})
    try:
        results = []
        for label, kind, text in proposals[:12]:
            attempt = workspace.score(bench.ws, bench.isolated, f"{name}_pad{len(results)}", text, conn=bench.conn,
                                      func=name, strategy="frame-pad-probe", model="zero-model")
            results.append((label, bool(attempt.compiled), bool(attempt.exact), attempt.score))
            if attempt.exact:
                break
    finally:
        bench.close()
    print(json.dumps({"function": name, "score": row["score"], "profiles_on_current": row["profiles_on_current"],
                      "deltas": stack_layout.stack_deltas(entry["diff"]), "proposals": len(kinds),
                      "results": results}), flush=True)
