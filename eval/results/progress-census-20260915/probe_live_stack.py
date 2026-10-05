"""Reproduce the live stack_layout job's inputs with the FROZEN campaign code: is `root.diff` usable?

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/progress-census-20260915/probe_live_stack.py osViBlack
"""
import json
import sys
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
sys.path.insert(0, str(RUN / "code"))
HERE = Path(__file__).resolve().parent

import importlib.util  # noqa: E402

# The bench helper exists only in the main tree; load it by path so every solver/eval import it makes resolves
# to the frozen campaign code first on sys.path.
_spec = importlib.util.spec_from_file_location("regalloc_probe", HERE.parents[1] / "regalloc_probe.py")
regalloc_probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(regalloc_probe)
from solver import stack_layout, workspace  # noqa: E402
from solver.fresh_compile import Names  # noqa: E402

pending = {r["function"]: r for r in json.loads((HERE / "pending.json").read_text())}
for name in sys.argv[1:]:
    row = pending[name]
    source = Path(row["source"]).read_text()
    bench = regalloc_probe.Bench({"name": name, "source": row["source"]})
    try:
        names = Names(bench.isolated, bench.ws)
        tag, root = names.score(f"{name}_agentrepair_root_1", source, conn=bench.conn, func=name, iteration=0,
                                strategy="probe-live-stack", model="zero-model", run_id=bench.run_id)
        diff = root.diff or ""
        proposals = list(stack_layout.variants(source, name, diff))
        print(json.dumps({"function": name, "module": stack_layout.__file__, "compiled": root.compiled,
                          "score": root.score, "diff_type": type(root.diff).__name__, "diff_chars": len(diff),
                          "diff_head": diff[:400], "deltas": stack_layout.stack_deltas(diff),
                          "proposals": [p[0] for p in proposals][:5]}, indent=1))
    finally:
        bench.close()
