"""Locate the pure-Python spin that stalled dominant-1 on guPerspectiveF / initMainMenuSceneModelParts.

    timeout 200 /home/grant/decomp/sbk1/.venv/bin/python eval/results/regalloc-20260913/hang_repro.py NAME

No compiles: feeds the recorded baseline diff/dump to the mutation generators
one family at a time, and dumps every thread's traceback if a stage exceeds 60 s.
"""
import faulthandler
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval import regalloc_probe  # noqa: E402
from solver import regalloc_mutations  # noqa: E402

name = sys.argv[1]
row = next(r for r in json.loads((ROOT / "eval/results/regalloc-20260913/dominant-sample.json").read_text())["functions"]
           if r["name"] == name)
bench = regalloc_probe.Bench(row)
try:
    source = Path(row["source"]).read_text()
    faulthandler.dump_traceback_later(60, exit=True)
    started = time.monotonic()
    base = bench.run(source, "baseline")
    print("baseline", base["score"], round(time.monotonic() - started, 1), flush=True)
    diff = base.get("_diff", "")
    m = regalloc_mutations
    families = [m.field_local_eliminations, m.struct_copy_merges, m.store_loops, m.guard_before_load,
                m.load_modify_stores, m.rotated_loops, m.readonly_field_local_inlines, m.store_value_locals,
                m.compound_assignments, m.self_update_temps, m.typed_index_scales, m.symbol_scale_fixes,
                m.negative_scale_splits, m.result_local_reuses, m.single_use_local_inlines, m.local_types,
                m.commutative_swaps, m.constant_local_inlines, m.statement_moves, m.declaration_swaps]
    for family in families:
        faulthandler.cancel_dump_traceback_later()
        faulthandler.dump_traceback_later(60, exit=True)
        started = time.monotonic()
        count = 0
        try:
            for _ in family(source, name):
                count += 1
        except (m.Decline, ValueError) as error:
            count = f"{count} ({type(error).__name__})"
        print(family.__name__, count, round(time.monotonic() - started, 2), flush=True)
    faulthandler.cancel_dump_traceback_later()
    faulthandler.dump_traceback_later(60, exit=True)
    started = time.monotonic()
    count = sum(1 for _ in regalloc_mutations.variants(source, name, diff))
    print("variants", count, round(time.monotonic() - started, 2), flush=True)
finally:
    faulthandler.cancel_dump_traceback_later()
    bench.close()
